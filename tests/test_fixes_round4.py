"""Regression tests for the fixes from the fourth full-codebase review.

Covers:
1. IMAP unsubscribe processed even when no campaign matches (not skipped/hidden).
2. IMAP search date window maintains a minimum 30-day floor.
3. Unsubscribes endpoints handle 'unknown' / unattributed campaign records.
4. Bounce ingest handles null subscriber lists.
5. Bounce ingest excludes MTA/daemon senders in free-text recipient extraction.
6. Auto-unblock tolerates null bounces data and null subscriber lists.
7. Campaign subscribers export tolerates null lists.
8. Unsubscribe reset handles null subscriber lists.
9. Bounce list preserves relative sort order across bounce types.
10. State files use explicit UTF-8 encoding.
"""

import asyncio
import email.message
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

NOW = datetime.now(timezone.utc)


@pytest.fixture
def client():
    from app.main import app
    with patch("app.main.verify_session", return_value=True):
        yield TestClient(app)


def _use_tmp_state(monkeypatch, tmp_path):
    import app.services.unsubscribe_log as shared_log
    monkeypatch.setattr(shared_log, "LOG_FILE", tmp_path / "log.json")
    monkeypatch.setattr(shared_log, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(shared_log, "PROCESSED_FILE", tmp_path / "processed.json")
    return shared_log


class _MockIMAP:
    def __init__(self, raws: dict):
        self.raws = raws
        self.stored: list = []
        self.search_criteria: list = []

    def select(self, mailbox, *args, **kwargs):
        return ("OK", [b"1"])

    def search(self, charset, criteria):
        self.search_criteria.append(criteria)
        return ("OK", [b" ".join(self.raws.keys())])

    def fetch(self, msg_id, spec):
        raw = self.raws.get(msg_id, b"")
        return ("OK", [(msg_id + b" (BODY[] {1})", raw), b")"])

    def store(self, msg_id, op, flags):
        self.stored.append((msg_id, op, flags))
        return ("OK", [b""])

    def logout(self):
        return ("BYE", [b""])


def _make_msg(sender, text, mid):
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = "inbox@example.com"
    msg["Subject"] = "Re: Hello"
    msg["Message-ID"] = mid
    msg["Date"] = "Mon, 01 Sep 2025 12:00:00 +0000"
    msg.set_content(text)
    return msg.as_bytes()


# ── 1. IMAP unsubscribe processed even when no campaign matches ─────────────

@pytest.mark.asyncio
async def test_imap_unsubscribe_unattributed_when_no_campaign_matches(tmp_path, monkeypatch):
    """When a subscriber asks to unsubscribe but was not on a campaign target list,
    they must still be unenrolled and logged as 'No matching campaign' instead of skipped.
    """
    state = _use_tmp_state(monkeypatch, tmp_path)
    from app.services import imap_unsubscribe as svc
    from app.services.listmonk_client import ListMonkClient

    conn = _MockIMAP({
        b"1": _make_msg("unsub@example.com", "Please remove me immediately", "<m1@test>"),
    })
    monkeypatch.setattr(svc, "connect_imap", lambda: conn)

    modified_lists = []

    async def mock_request(method, path, **kwargs):
        if method == "GET" and "/api/campaigns" in path:
            return {"data": {"results": [{
                "id": 10, "name": "Campaign A", "status": "finished",
                "created_at": "2025-09-01", "lists": [{"id": 1}],
            }], "total": 1}}
        if method == "GET" and "/api/subscribers" in path:
            # Subscriber is on list 99 (not list 1)
            return {"data": {"results": [{
                "id": 42, "email": "unsub@example.com", "name": "Test User",
                "lists": [{"id": 99, "name": "Other List"}],
            }], "total": 1}}
        if method == "PUT" and path == "/api/subscribers/lists":
            modified_lists.append(kwargs.get("json"))
            return {"data": True}
        return {"data": {}}

    lm = ListMonkClient.__new__(ListMonkClient)
    lm._client = None
    lm._request = mock_request

    res = await svc.scan_and_unsubscribe(lm)
    assert res["processed"] == 1

    # Check unenrollment was sent
    assert len(modified_lists) == 1
    assert modified_lists[0]["ids"] == [42]
    assert modified_lists[0]["target_list_ids"] == [99]
    assert modified_lists[0]["action"] == "unsubscribe"

    # Check log contains unattributed record
    records = state.load_log()
    assert len(records) == 1
    assert records[0]["email"] == "unsub@example.com"
    assert records[0]["campaign_id"] is None
    assert records[0]["campaign_name"] == "No matching campaign"

    # Check IMAP message was marked \Seen
    assert any(r"\Seen" in f for _, _, f in conn.stored)


# ── 2. IMAP search date window maintains a minimum 30-day floor ──────────────

@pytest.mark.asyncio
async def test_imap_search_date_window_has_30_day_minimum(tmp_path, monkeypatch):
    """If latest campaign was on the 1st of the current month, the search query
    must still search at least 30 days back to avoid dropping unread replies.
    """
    _use_tmp_state(monkeypatch, tmp_path)
    from app.services import imap_unsubscribe as svc
    from app.services.listmonk_client import ListMonkClient

    conn = _MockIMAP({})
    monkeypatch.setattr(svc, "connect_imap", lambda: conn)

    # Mock campaign sent on the 1st of current month
    recent_first_of_month = datetime.now(timezone.utc).replace(day=1)

    async def mock_request(method, path, **kwargs):
        if method == "GET" and "/api/campaigns" in path:
            return {"data": {"results": [{
                "id": 10, "name": "Current Month", "status": "finished",
                "started_at": recent_first_of_month.isoformat(),
                "lists": [{"id": 1}],
            }], "total": 1}}
        return {"data": {"results": [], "total": 0}}

    lm = ListMonkClient.__new__(ListMonkClient)
    lm._client = None
    lm._request = mock_request

    await svc.scan_and_unsubscribe(lm)
    assert len(conn.search_criteria) == 1
    criteria = conn.search_criteria[0]
    # Criteria format: '(UNSEEN SINCE DD-Mon-YYYY)'
    assert criteria.startswith("(UNSEEN SINCE ")
    # The search date string must correspond to at least 30 days ago
    date_part = criteria.replace("(UNSEEN SINCE ", "").replace(")", "").strip()
    parsed_since = datetime.strptime(date_part, "%d-%b-%Y").replace(tzinfo=timezone.utc)
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    # parsed_since should be <= thirty_days_ago + 1 day
    assert parsed_since.date() <= (thirty_days_ago + timedelta(days=1)).date()


# ── 3. Unsubscribes endpoints handle 'unknown' / unattributed campaign records ─

def test_unsubscribes_unknown_campaign_endpoints(client, tmp_path, monkeypatch):
    """GET /campaign/unknown, export, and delete must work without 422 errors."""
    state = _use_tmp_state(monkeypatch, tmp_path)
    records = [
        {
            "email": "user1@example.com",
            "name": "User 1",
            "source": "email",
            "campaign_id": None,
            "campaign_name": "No matching campaign",
            "campaign_key": "2026-03",
            "timestamp": "2026-03-01T12:00:00Z",
        },
        {
            "email": "user2@example.com",
            "name": "User 2",
            "source": "link",
            "campaign_id": 5,
            "campaign_name": "Campaign Five",
            "campaign_key": "2026-03",
            "timestamp": "2026-03-02T12:00:00Z",
        },
    ]
    state.save_log(records)

    # 1. Fetch unknown campaign records
    r = client.get("/api/unsubscribes/campaign/unknown")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["results"][0]["email"] == "user1@example.com"

    # 2. Export unknown campaign CSV
    r_exp = client.get("/api/unsubscribes/campaign/unknown/export")
    assert r_exp.status_code == 200
    assert "user1@example.com" in r_exp.text
    assert "user2@example.com" not in r_exp.text

    # 3. Delete unknown campaign records
    r_del = client.delete("/api/unsubscribes/campaign/unknown")
    assert r_del.status_code == 200
    assert r_del.json()["removed"] == 1

    # Only user2 remains
    rem = state.load_log()
    assert len(rem) == 1
    assert rem[0]["email"] == "user2@example.com"


# ── 4. Bounce ingest handles null subscriber lists ───────────────────────────

@pytest.mark.asyncio
async def test_bounce_ingest_null_subscriber_lists(monkeypatch):
    """When ListMonk returns 'lists': null for a subscriber, bounce ingest must
    not raise TypeError."""
    from app.services import bounce_ingest as svc
    from app.services.listmonk_client import ListMonkClient

    raw_dsn = (
        b"From: mailer-daemon@example.com\r\n"
        b"To: bounce@example.com\r\n"
        b"Subject: Delivery Failure\r\n"
        b"Content-Type: multipart/report; report-type=delivery-status; boundary=B\r\n\r\n"
        b"--B\r\nContent-Type: text/plain\r\n\r\nFailed\r\n"
        b"--B\r\nContent-Type: message/delivery-status\r\n\r\n"
        b"Action: failed\r\nStatus: 5.1.1\r\nFinal-Recipient: rfc822; dead@example.com\r\n"
        b"--B--\r\n"
    )

    conn = _MockIMAP({b"1": raw_dsn})
    monkeypatch.setattr(svc, "connect_bounce_imap_rw", lambda: conn)

    async def mock_request(method, path, **kwargs):
        if method == "GET" and "/api/campaigns" in path:
            return {"data": {"results": [], "total": 0}}
        if method == "GET" and "/api/subscribers" in path:
            # lists is explicitly None
            return {"data": {"results": [{
                "id": 100, "email": "dead@example.com", "lists": None,
            }], "total": 1}}
        return {"data": {}}

    lm = ListMonkClient.__new__(ListMonkClient)
    lm._client = None
    lm._request = mock_request

    # Should run and skip without crashing
    res = await svc.ingest_bounce_mailbox(lm)
    assert res["scanned"] == 1


# ── 5. Bounce ingest excludes MTA/daemon senders in free-text ────────────────

def test_bounce_ingest_ignores_mailer_daemon_in_free_text():
    from app.services.bounce_ingest import _extract_bounced_recipient

    msg = email.message.EmailMessage()
    msg["From"] = "mailer-daemon@mx.google.com"
    msg["To"] = "bounce@example.com"
    msg["Subject"] = "Undelivered Mail"

    body = (
        "This is an automatically generated Delivery Status Notification.\n\n"
        "Delivery to the following recipient failed permanently:\n\n"
        "    realuser@target.com\n\n"
        "Technical details of permanent failure:\n"
        "Google tried to deliver your message, but it was rejected by the server."
    )
    recipient = _extract_bounced_recipient(msg, body)
    assert recipient == "realuser@target.com"


# ── 6. Auto-unblock tolerates null bounces data and null subscriber lists ────

@pytest.mark.asyncio
async def test_auto_unblock_tolerates_null_data_and_lists():
    from app.services.auto_unblock import unblock_subscribers, delete_bounce_records_for_subscribers
    from app.services.listmonk_client import ListMonkClient

    updated_subs = []

    async def mock_request(method, path, **kwargs):
        if method == "GET" and "/bounces" in path:
            # ListMonk returns data: null
            return {"data": None}
        if method == "PUT" and "/api/subscribers/" in path:
            updated_subs.append(kwargs.get("json"))
            return {"data": True}
        return {"data": {}}

    lm = ListMonkClient.__new__(ListMonkClient)
    lm._client = None
    lm._request = mock_request

    # 1. delete_bounce_records_for_subscribers with null data
    deleted = await delete_bounce_records_for_subscribers(lm, [1, 2])
    assert deleted == 0

    # 2. unblock_subscribers with subscriber having lists=None
    res = await unblock_subscribers(lm, [{
        "id": 1,
        "email": "user@example.com",
        "name": None,
        "lists": None,
        "attribs": None,
    }])
    assert res["success"] == 1
    assert len(updated_subs) == 1
    assert updated_subs[0]["lists"] == []
    assert updated_subs[0]["name"] == ""
    assert updated_subs[0]["attribs"] == {}


# ── 7. Campaign subscribers export tolerates null lists ──────────────────────

@pytest.mark.asyncio
async def test_campaign_subscribers_csv_export_null_lists(client, monkeypatch):
    """When subscriber lists is None in export subscribers, do not raise TypeError."""
    from app.routers import campaigns as camp_router

    async def mock_paginate(fn, per_page, query):
        return [
            {"id": 1, "email": "a@x.com", "name": "A", "status": "enabled", "lists": None, "attribs": {}, "created_at": "2026-01-01"},
        ]

    monkeypatch.setattr(camp_router.listmonk, "paginate_all", mock_paginate)
    r = client.get("/api/campaigns/1/subscribers/views/export")
    assert r.status_code == 200
    assert "a@x.com" in r.text


# ── 8. Unsubscribe reset handles null subscriber lists ───────────────────────

@pytest.mark.asyncio
async def test_unsubscribe_reset_null_subscriber_lists(client, tmp_path, monkeypatch):
    """When resetting an unsubscribe and the subscriber in ListMonk has lists=None,
    it must not crash."""
    state = _use_tmp_state(monkeypatch, tmp_path)
    from app.routers import unsubscribes as unsub_router

    state.save_log([{
        "email": "reset@example.com",
        "subscriber_id": 55,
        "lists_removed": [1, 2],
        "timestamp": "2026-01-01T00:00:00Z",
    }])

    updated = []

    async def mock_request(method, path, **kwargs):
        if method == "GET" and path == "/api/subscribers/55":
            return {"data": {
                "id": 55, "email": "reset@example.com", "name": "Reset User",
                "lists": None,  # Explicitly null
            }}
        if method == "PUT" and path == "/api/subscribers/55":
            updated.append(kwargs.get("json"))
            return {"data": True}
        if method == "PUT" and path == "/api/subscribers/lists":
            return {"data": True}
        return {"data": {}}

    monkeypatch.setattr(unsub_router.listmonk, "_request", mock_request)

    r = client.post("/api/unsubscribes/reset")
    assert r.status_code == 200
    assert r.json()["restored"] == 1
    assert len(updated) == 1
    assert updated[0]["lists"] == []


# ── 9. Bounce list preserves relative sort order across bounce types ─────────

@pytest.mark.asyncio
async def test_bounce_list_maintains_interleaved_sort_order(monkeypatch):
    """Bounces returned from ListMonk in mixed hard/soft order must keep their order
    rather than grouping all hard bounces first."""
    from app.services.bounce_list import fetch_all_filtered_bounces
    from app.services.listmonk_client import ListMonkClient

    raw_bounces = [
        {"id": 1, "email": "hard1@x.com", "type": "hard", "campaign": {"id": 10}},
        {"id": 2, "email": "soft1@x.com", "type": "soft", "campaign": {"id": 10}},
        {"id": 3, "email": "hard2@x.com", "type": "hard", "campaign": {"id": 10}},
        {"id": 4, "email": "soft2@x.com", "type": "soft", "campaign": {"id": 10}},
    ]

    async def mock_paginate(fn, per_page, **kwargs):
        return list(raw_bounces)

    lm = ListMonkClient.__new__(ListMonkClient)
    lm._client = None
    lm.paginate_all = mock_paginate

    # None of them opened the campaign
    with patch("app.services.bounce_list.filter_bounces_excluding_openers_fast", return_value=[raw_bounces[0], raw_bounces[2]]):
        res = await fetch_all_filtered_bounces(lm, bounce_type="")

    # Must preserve [hard1, soft1, hard2, soft2]
    assert [b["id"] for b in res] == [1, 2, 3, 4]


# ── 10. State files use explicit UTF-8 encoding ──────────────────────────────

def test_state_files_utf8_encoding(tmp_path, monkeypatch):
    """Reading state files with non-ASCII characters must not fail with UnicodeDecodeError."""
    state = _use_tmp_state(monkeypatch, tmp_path)

    unicode_name = "François René 🚀"
    state.save_log([{
        "email": "francois@example.com",
        "name": unicode_name,
        "campaign_name": "Campagne Spéciale",
    }])

    loaded = state.load_log()
    assert len(loaded) == 1
    assert loaded[0]["name"] == unicode_name

    from app.services.campaign_scheduler import save_schedule, load_schedule
    monkeypatch.setattr("app.services.campaign_scheduler.SCHEDULE_FILE", tmp_path / "sched.json")

    save_schedule({"timezone": "Europe/Paris", "comment": "Tâche programmée"})
    sched = load_schedule()
    assert sched["comment"] == "Tâche programmée"


# ── 11. Edge cases in exports, bulk blocklist, and delete bounces ────────────

@pytest.mark.asyncio
async def test_export_campaign_analytics_handles_non_list_or_null_data(client, monkeypatch):
    from app.routers import campaigns as camp_router

    async def mock_analytics(*args, **kwargs):
        # Upstream returns dict instead of list
        return {"data": {"summary": 123}}

    monkeypatch.setattr(camp_router.listmonk, "get_campaign_analytics", mock_analytics)
    r = client.get("/api/campaigns/analytics/views/export")
    assert r.status_code == 404
    assert r.json()["detail"] == "No analytics data found"


@pytest.mark.asyncio
async def test_blocklist_subscribers_tolerates_null_ids(client, monkeypatch):
    from app.routers import subscribers as sub_router

    called_ids = []

    async def mock_blocklist(ids):
        called_ids.append(ids)
        return {"data": True}

    monkeypatch.setattr(sub_router.listmonk, "blocklist_subscribers", mock_blocklist)
    r = client.put("/api/subscribers/blocklist", json={"ids": None})
    assert r.status_code == 200
    assert called_ids == [[]]


@pytest.mark.asyncio
async def test_delete_all_bounces_filters_missing_ids(client, monkeypatch):
    from app.routers import bounces as bounce_router

    # Bounce records with missing or null ids
    async def mock_fetch_all(*args, **kwargs):
        return [{"id": 1}, {"id": None}, {}]

    deleted_ids = []

    async def mock_delete_bounce(bid):
        deleted_ids.append(bid)
        return {"data": True}

    monkeypatch.setattr(bounce_router, "fetch_all_filtered_bounces", mock_fetch_all)
    monkeypatch.setattr(bounce_router.listmonk, "delete_bounce", mock_delete_bounce)

    r = client.delete("/api/bounces?campaign_id=10")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
    assert deleted_ids == [1]

