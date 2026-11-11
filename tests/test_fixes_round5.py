"""Regression tests for the fifth review's fixes."""

import email
import email.policy
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.main.verify_session", lambda request: True)
        yield TestClient(app)


# ── Subscribers ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_subscriber_merges_existing_lists(monkeypatch):
    """ListMonk PUT clears omitted lists; the proxy must preserve them."""
    from app.routers import subscribers as S

    calls = {}

    class LM:
        async def get_subscriber(self, sid):
            return {"data": {"id": sid, "lists": [{"id": 2}, {"id": 5}]}}

        async def update_subscriber(self, sid, data):
            calls["data"] = data
            return {"data": True}

    monkeypatch.setattr(S, "listmonk", LM())
    await S.update_subscriber(9, {
        "email": "x@example.com", "name": "X", "status": "enabled", "attribs": {},
    })
    assert calls["data"]["lists"] == [2, 5]


@pytest.mark.asyncio
async def test_put_subscriber_keeps_explicit_lists(monkeypatch):
    from app.routers import subscribers as S

    calls = {}

    class C:
        async def update_subscriber(self, sid, data):
            calls["data"] = data
            return {"data": True}

    monkeypatch.setattr(S, "listmonk", C())
    await S.update_subscriber(9, {"lists": [7]})
    assert calls["data"]["lists"] == [7]


@pytest.mark.asyncio
async def test_subscriber_search_is_translated_to_safe_expression(monkeypatch):
    from app.routers import subscribers as S

    seen = {}

    class C:
        async def get_subscribers(self, page, per_page, query, list_id):
            seen["query"] = query
            return {"data": {"results": [], "total": 0}}

    monkeypatch.setattr(S, "listmonk", C())
    await S.get_subscribers(page=1, per_page=50, query="", search="o'brien", list_id=None)
    assert "strpos" in seen["query"]
    assert "o''brien" in seen["query"]


def test_subscriber_search_helper_escapes_quotes():
    from app.services.imap_helpers import subscriber_search_query
    q = subscriber_search_query("a'b")
    assert "a''b" in q
    assert q.count("subscribers.") >= 2


# ── Bounce ingestion ─────────────────────────────────────────────────────────

def test_bounce_fetch_uses_body_peek():
    import inspect
    from app.services import bounce_ingest
    src = inspect.getsource(bounce_ingest.ingest_bounce_mailbox)
    assert "(BODY.PEEK[])" in src
    assert '"(RFC822)"' not in src


def test_text_rfc822_headers_campaign_hint():
    from app.services.bounce_ingest import _extract_campaign_hint
    raw = b'''From: a@b.c
Subject: bounce
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: text/rfc822-headers

X-Listmonk-Campaign: camp-uuid

--B
Content-Type: message/delivery-status

Final-Recipient: rfc822; user@example.com
Status: 5.1.1

--B--
'''
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert _extract_campaign_hint(msg) == "camp-uuid"


def test_relay_access_denied_is_soft():
    from app.services.bounce_ingest import classify_bounce
    assert classify_bounce("550 5.7.1 relay access denied")["type"] == "soft"


def test_recipient_status_text_is_per_recipient():
    from app.services.bounce_ingest import _recipient_status_text
    raw = b'''From: a@b.c
Subject: bounce
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: message/delivery-status

Final-Recipient: rfc822; soft@example.com
Status: 4.4.1

Final-Recipient: rfc822; hard@example.com
Status: 5.1.1

--B--
'''
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    soft = _recipient_status_text(msg, "soft@example.com")
    hard = _recipient_status_text(msg, "hard@example.com")
    assert "4.4.1" in soft and "5.1.1" not in soft
    assert "5.1.1" in hard


# ── Auto-unblock ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_unblock_resubscribes_to_lists():
    from app.services.auto_unblock import unblock_subscribers

    calls = []

    class LM:
        async def modify_list_memberships(self, data):
            calls.append(("lists", data))

        async def update_subscriber(self, sid, data):
            calls.append(("update", data))

        async def get_subscriber_bounces(self, sid):
            return {"data": []}

    res = await unblock_subscribers(LM(), [{
        "id": 5, "email": "a@example.com", "name": "", "attribs": {},
        "lists": [{"id": 2}, {"id": 3}],
    }])
    assert res["success"] == 1
    assert calls[0][0] == "lists"
    assert calls[0][1]["action"] == "add"
    assert calls[0][1]["target_list_ids"] == [2, 3]
    assert calls[1][0] == "update"


# ── Campaigns / auth / headers ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_archive_client_sends_archive_true():
    from app.services.listmonk_client import ListMonkClient

    lm = ListMonkClient()
    lm._request = AsyncMock(return_value={"data": True})
    await lm.archive_campaign(7)
    args, kwargs = lm._request.await_args
    assert kwargs["json"] == {"archive": True}


@pytest.mark.asyncio
async def test_campaign_list_requests_no_body():
    from app.services.listmonk_client import ListMonkClient

    lm = ListMonkClient()
    lm._request = AsyncMock(return_value={"data": {}})
    await lm.get_campaigns(no_body=True)
    _args, kwargs = lm._request.await_args
    assert kwargs["params"]["no_body"] == "true"


def test_login_with_non_string_fields_is_400(client):
    r = client.post("/auth/login", json={"username": 123, "password": "x"})
    assert r.status_code == 400


def test_export_filename_is_sanitised(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers import bounces as B

    monkeypatch.setattr(B, "fetch_all_filtered_bounces", AsyncMock(return_value=[
        {"id": 1, "email": "a@b.com", "type": "hard", "created_at": "2026-01-01"},
    ]))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.main.verify_session", lambda request: True)
        c = TestClient(app)
        r = c.get("/api/bounces/export", params={
            "bounce_type": "hard\r\nX-Injected: yes",
        })
    assert r.status_code == 200
    assert "\r" not in r.headers["content-disposition"]
    assert "\n" not in r.headers["content-disposition"]


# ── Bounce pagination / cache ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hard_bounce_total_keeps_pagination_moving(monkeypatch):
    from app.services import bounce_list as bl

    rows = [
        {"id": i, "email": f"u{i}@x.com", "type": "hard",
         "campaign": {"id": 1}}
        for i in range(100)
    ]

    class C:
        async def get_bounces(self, page, per_page, campaign_id=None,
                              source="", bounce_type=""):
            start = (page - 1) * per_page
            return {"data": {"results": rows[start:start + per_page],
                             "total": len(rows)}}

    # Stale/small cache: previously total was clamped to len(collected),
    # hiding every page after the first.
    async def passthrough(client, bounces):
        return bounces

    monkeypatch.setattr(bl, "filter_bounces_excluding_openers_fast", passthrough)
    monkeypatch.setattr(bl, "estimate_filtered_hard_total", lambda cid: 5)
    first = await bl.fetch_filtered_bounces_page(C(), 1, 25, "hard")
    assert first["data"]["total"] > 25
    second = await bl.fetch_filtered_bounces_page(C(), 2, 25, "hard")
    assert len(second["data"]["results"]) == 25


# ── State files / conversion / attribution / replies ────────────────────────

def test_load_settings_wrong_shape_and_unicode(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as ul

    p = tmp_path / "settings.json"
    monkeypatch.setattr(ul, "SETTINGS_FILE", p)
    p.write_text("[]")
    assert ul.load_settings()["blocklist_enabled"] is False
    p.write_bytes(b"\xff")
    assert ul.load_settings()["blocklist_enabled"] is False
    p.write_text(json.dumps([1, "x"]))
    assert ul.load_log() == []


def test_load_log_non_string_campaign_key_does_not_crash(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as ul

    p = tmp_path / "log.json"
    monkeypatch.setattr(ul, "LOG_FILE", p)
    p.write_text(json.dumps([{"email": "a@x.com", "campaign_key": 123}]))
    assert ul.load_log()[0]["campaign_key"] == ""


def test_utf16_csv_headers_are_detected():
    from app.services.csv_converter import detect_columns
    result = detect_columns("email,name\na@b.com,Alice\n".encode("utf-16"))
    assert result["columns"] == ["email", "name"]


def test_link_trigger_lists_ignore_stale_memberships():
    from app.services.link_unsubscribe import _trigger_list_ids

    now = datetime.now(timezone.utc).timestamp()
    sub = {"lists": [
        {"id": 1, "subscription_status": "unsubscribed",
         "subscription_updated_at": "2020-01-01T00:00:00+00:00"},
        {"id": 2, "subscription_status": "unsubscribed",
         "subscription_updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat()},
    ]}
    assert _trigger_list_ids(sub, {1, 2}, now - 1000) == {2}


def test_bottom_posted_reply_is_found():
    from app.services.imap_unsubscribe import _extract_reply_only
    body = (
        "On Mon, Jan 1, 2026 at 12:00 PM Support <s@x.com> wrote:\n"
        "> Thanks for subscribing.\n"
        "> Reply with remove me to leave.\n"
        "\n"
        "Please remove me from this list.\n"
    )
    assert "remove me" in _extract_reply_only(body).lower()


def test_opener_cache_sweeps_unreferenced_expired_campaigns():
    import time
    from app.services import opener_cache as oc

    oc._cache[1] = {"old@x.com"}
    oc._cache[2] = {"new@x.com"}
    oc._fetched_at[1] = time.monotonic() - oc.TTL_SECONDS - 1
    oc._fetched_at[2] = time.monotonic()
    oc._sweep_expired()
    assert 1 not in oc._cache and 2 in oc._cache


@pytest.mark.asyncio
async def test_reset_counts_duplicate_records_correctly(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as ul
    from app.routers import unsubscribes as U

    monkeypatch.setattr(ul, "LOG_FILE", tmp_path / "log.json")
    monkeypatch.setattr(ul, "PROCESSED_FILE", tmp_path / "p.json")
    monkeypatch.setattr(ul, "SETTINGS_FILE", tmp_path / "s.json")
    ul.save_log([
        {"email": "same@x.com", "subscriber_id": 1, "lists_removed": [1]},
        {"email": "same@x.com", "subscriber_id": 1, "lists_removed": [2]},
    ])

    calls = []

    class LM:
        async def get_subscriber(self, sid):
            calls.append("get")
            return {"data": {"id": sid, "email": "same@x.com", "name": "",
                             "lists": [{"id": 1}], "attribs": {}}}

        async def update_subscriber(self, sid, data):
            pass

        async def modify_list_memberships(self, data):
            calls.append("lists")

    monkeypatch.setattr(U, "listmonk", LM())
    res = await U.reset_all_unsubscribes()
    assert res["restored"] == 2 and res["failed"] == 0
    assert calls.count("get") == 1
    assert ul.load_log() == []


@pytest.mark.asyncio
async def test_scheduler_persists_auto_pause_and_respects_manual_pause(tmp_path, monkeypatch):
    from app.services import campaign_scheduler as sched

    path = tmp_path / "schedule.json"
    monkeypatch.setattr(sched, "SCHEDULE_FILE", path)
    sched.save_schedule({
        "enabled": True, "timezone": "UTC",
        "start_hour": 9, "start_minute": 0,
        "end_hour": 17, "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "auto_paused_campaigns": [],
        "auto_paused_updated_at": {},
    })

    paused_at = "2026-01-01T10:00:00+00:00"
    calls = []

    class C:
        get_campaigns = None

        async def paginate_all(self, fn, per_page=100, status="", **kwargs):
            if status == "running":
                return [{"id": 1, "name": "C", "status": "running",
                         "updated_at": paused_at}]
            return []

        async def change_campaign_status(self, cid, status):
            calls.append(status)
            if status == "paused":
                return {"data": {"id": cid, "status": "paused",
                                 "updated_at": paused_at}}
            return {"data": {"id": cid, "status": status,
                             "updated_at": "2026-01-01T12:00:00+00:00"}}

    monkeypatch.setattr(sched, "is_within_send_window", lambda schedule: False)
    await sched.run_scheduler_tick(C())
    saved = json.loads(path.read_text())
    assert saved["auto_paused_campaigns"] == [1]
    assert saved["auto_paused_updated_at"]["1"] == paused_at

    # Now "in window", but the campaign's updated_at changed after our pause:
    # that means a human touched it, so it must stay paused.
    monkeypatch.setattr(sched, "is_within_send_window", lambda schedule: True)

    class C2:
        get_campaigns = None

        async def paginate_all(self, fn, per_page=100, status="", **kwargs):
            if status == "paused":
                return [{"id": 1, "name": "C", "status": "paused",
                         "updated_at": "2026-01-01T12:00:00+00:00"}]
            return []

        async def change_campaign_status(self, cid, status):
            calls.append(status)

    await sched.run_scheduler_tick(C2())
    saved = json.loads(path.read_text())
    assert saved["auto_paused_campaigns"] == []
    assert "running" not in calls


@pytest.mark.asyncio
async def test_auto_unblock_does_not_restore_older_deliberate_unsubscribe():
    from app.services.auto_unblock import unblock_subscribers

    calls = []
    updated = {}

    class LM:
        async def modify_list_memberships(self, data):
            calls.append(data)

        async def update_subscriber(self, sid, data):
            updated.update(data)

        async def get_subscriber_bounces(self, sid):
            return {"data": []}

    await unblock_subscribers(LM(), [{
        "id": 5, "email": "a@example.com", "name": "", "attribs": {},
        "updated_at": "2026-01-02T00:00:00+00:00",
        "lists": [
            {"id": 1, "subscription_updated_at": "2025-01-01T00:00:00+00:00"},
            {"id": 2, "subscription_updated_at": "2026-01-02T00:00:00+00:00"},
        ],
    }])
    assert calls and calls[0]["target_list_ids"] == [2]
    # The PUT must retain both memberships; omitting list 1 would delete it.
    assert updated["lists"] == [1, 2]
