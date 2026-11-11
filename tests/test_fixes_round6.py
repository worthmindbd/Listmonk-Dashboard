"""Regression tests for the sixth review's fixes.

Covers:
1. Untrusted X-Forwarded-Proto spoofing (Secure cookie & HSTS poisoning)
2. Cross-site mutation blocking (Sec-Fetch-Site: cross-site and mismatched Origin)
3. Campaign PUT preserving omitted fields (content_type, type, template_id, lists)
4. Campaign mutations invalidating summary/opener/hard-bounce caches
5. Subscriber partial PUT backfilling omitted fields (email, name, status, attribs, lists)
6. IMAP unsubscribe persisting audit record when blocklist_subscriber fails
7. Async locked unsubscribe settings read/write (aload_settings / asave_settings)
8. Link unsubscribe campaign history sorting by send date and respecting total
9. In-memory revocation pruning on _refresh_revoked and UTC-aware hard_bounce_cache timestamp
10. Frontend regression checks (error contract in triggerScan/resetAll, escaping, chart cleanup, campaign edit)
"""

import asyncio
import email.message
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    from app.main import app
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.main.verify_session", lambda request: True)
        yield TestClient(app)


# ── 1. Untrusted X-Forwarded-Proto spoofing ──────────────────────────────────

def _req_with_proto(peer: str, proto: str = "https") -> Request:
    return Request({
        "type": "http",
        "scheme": "http",
        "method": "GET",
        "path": "/auth/login",
        "headers": [(b"host", b"example.com"), (b"x-forwarded-proto", proto.encode())],
        "client": (peer, 12345),
    })


def test_x_forwarded_proto_ignored_from_untrusted_public_peer(monkeypatch):
    from app import auth
    from app.main import _is_https

    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)

    untrusted_req = _req_with_proto("8.8.8.8", "https")
    assert _is_https(untrusted_req) is False
    assert auth._is_secure_cookie(untrusted_req) is False

    trusted_req = _req_with_proto("127.0.0.1", "https")
    assert _is_https(trusted_req) is True
    assert auth._is_secure_cookie(trusted_req) is True


def test_x_forwarded_proto_respects_trust_proxy_env(monkeypatch):
    from app import auth
    from app.main import _is_https

    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    untrusted_req = _req_with_proto("8.8.8.8", "https")

    monkeypatch.setenv("TRUST_PROXY", "true")
    assert _is_https(untrusted_req) is True
    assert auth._is_secure_cookie(untrusted_req) is True

    monkeypatch.setenv("TRUST_PROXY", "false")
    loopback_req = _req_with_proto("127.0.0.1", "https")
    assert _is_https(loopback_req) is False
    assert auth._is_secure_cookie(loopback_req) is False


# ── 2. Cross-site mutation blocking ──────────────────────────────────────────

def test_cross_site_sec_fetch_site_blocks_state_changing_requests(client):
    resp = client.post(
        "/auth/logout",
        headers={"Sec-Fetch-Site": "cross-site"},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    api_resp = client.Delete(
        "/api/bounces/1",
        headers={"Sec-Fetch-Site": "cross-site"},
    ) if hasattr(client, "Delete") else client.delete(
        "/api/bounces/1",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert api_resp.status_code == 403


def test_mismatched_origin_blocks_state_changing_requests(client):
    resp = client.post(
        "/auth/logout",
        headers={"Host": "dashboard.internal", "Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    same_origin = client.post(
        "/auth/logout",
        headers={"Host": "dashboard.internal", "Origin": "https://dashboard.internal"},
        follow_redirects=False,
    )
    assert same_origin.status_code == 303


# ── 3 & 4. Campaign PUT field preservation & cache invalidation ──────────────

@pytest.mark.asyncio
async def test_update_campaign_preserves_omitted_fields(monkeypatch):
    from app.routers import campaigns as C

    sent_payload = {}

    class FakeLM:
        async def get_campaign(self, cid):
            return {
                "data": {
                    "id": cid,
                    "name": "Old Name",
                    "subject": "Old Subject",
                    "body": "<p>Hi</p>",
                    "type": "regular",
                    "content_type": "html",
                    "template_id": 4,
                    "messenger": "email",
                    "from_email": "news@example.com",
                    "lists": [{"id": 2, "name": "Main"}],
                    "tags": ["t1"],
                }
            }

        async def update_campaign(self, cid, data):
            sent_payload.update(data)
            return {"data": data}

    monkeypatch.setattr(C, "listmonk", FakeLM())
    await C.update_campaign(10, {"name": "New Name", "subject": "New Subject", "body": "<p>New</p>"})

    assert sent_payload["content_type"] == "html"
    assert sent_payload["template_id"] == 4
    assert sent_payload["type"] == "regular"
    assert sent_payload["lists"] == [2]
    assert sent_payload["messenger"] == "email"


def test_campaign_mutations_invalidate_caches(client, monkeypatch):
    from app.routers import campaigns as C
    from app.services import hard_bounce_cache as hbc
    from app.services import opener_cache

    scheduled = []
    monkeypatch.setattr(C, "schedule_hard_bounce_update", lambda: scheduled.append(True))

    C._summary_cache = {"data": {"total_campaigns": 5}}
    C._summary_cached_at = time.monotonic()
    opener_cache._cache[77] = {"a@example.com"}
    opener_cache._fetched_at[77] = time.monotonic()
    hbc._hard_bounce_counts[77] = 12

    monkeypatch.setattr(C.listmonk, "delete_campaign", AsyncMock(return_value={"data": True}))
    resp = client.delete("/api/campaigns/77")
    assert resp.status_code == 200
    assert C._summary_cache == {}
    assert 77 not in opener_cache._cache
    assert 77 not in hbc._hard_bounce_counts
    assert scheduled == [True]


# ── 5. Subscriber partial PUT backfills omitted fields ───────────────────────

@pytest.mark.asyncio
async def test_put_subscriber_backfills_omitted_fields(monkeypatch):
    from app.routers import subscribers as S

    captured = {}

    class FakeLM:
        async def get_subscriber(self, sid):
            return {
                "data": {
                    "id": sid,
                    "email": "keep@example.com",
                    "name": "Keep Me",
                    "status": "enabled",
                    "attribs": {"role": "vip"},
                    "lists": [{"id": 3}],
                }
            }

        async def update_subscriber(self, sid, data):
            captured.update(data)
            return {"data": data}

    monkeypatch.setattr(S, "listmonk", FakeLM())
    await S.update_subscriber(42, {"lists": [9]})
    assert captured == {
        "email": "keep@example.com",
        "name": "Keep Me",
        "status": "enabled",
        "attribs": {"role": "vip"},
        "lists": [9],
    }


# ── 6. IMAP unsubscribe logs record even if blocklist_subscriber fails ───────

@pytest.mark.asyncio
async def test_imap_unsubscribe_persists_record_when_blocklist_fails(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as shared_log
    from app.services import imap_unsubscribe as svc

    monkeypatch.setattr(shared_log, "LOG_FILE", tmp_path / "log.json")
    monkeypatch.setattr(shared_log, "PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(shared_log, "SETTINGS_FILE", tmp_path / "settings.json")
    shared_log.save_settings({"blocklist_enabled": True})

    msg = email.message.EmailMessage()
    msg["From"] = "unsub@example.com"
    msg["To"] = "inbox@example.com"
    msg["Subject"] = "Re: News"
    msg["Message-ID"] = "<msg-bl-fail@example.com>"
    msg["Date"] = "Mon, 01 Sep 2026 12:00:00 +0000"
    msg.set_content("Please remove me")
    raw_bytes = msg.as_bytes()

    stored = []

    class Conn:
        def select(self, mailbox):
            return ("OK", [b"1"])

        def search(self, charset, criteria):
            return ("OK", [b"1"])

        def fetch(self, mid, spec):
            return ("OK", [(b"1 (BODY[] {1})", raw_bytes), b")"])

        def store(self, mid, op, flags):
            stored.append(mid)
            return ("OK", [b""])

        def logout(self):
            return ("BYE", [b""])

    class FakeClient:
        async def get_campaigns(self, page=1, **kw):
            return {"data": {"results": [{
                "id": 11, "name": "Sept", "status": "finished",
                "created_at": "2026-09-01T00:00:00Z",
                "started_at": "2026-09-01T01:00:00Z",
                "lists": [{"id": 4}],
            }], "total": 1}}

        async def get_subscribers(self, *a, **kw):
            return {"data": {"results": [{
                "id": 99, "email": "unsub@example.com", "name": "User",
                "lists": [{"id": 4}],
            }], "total": 1}}

        async def modify_list_memberships(self, data):
            return {"data": True}

        async def blocklist_subscriber(self, sub_id):
            raise RuntimeError("blocklist endpoint timeout")

    monkeypatch.setattr(svc, "connect_imap", lambda: Conn())
    result = await svc.scan_and_unsubscribe(FakeClient())

    assert result["processed"] == 1
    assert result["errors"] == 1
    assert stored == [b"1"]
    records = shared_log.load_log()
    assert len(records) == 1
    assert records[0]["email"] == "unsub@example.com"
    assert records[0]["lists_removed"] == [4]


# ── 7. Async locked unsubscribe settings ─────────────────────────────────────

@pytest.mark.asyncio
async def test_asave_and_aload_settings_roundtrip(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as shared_log

    monkeypatch.setattr(shared_log, "SETTINGS_FILE", tmp_path / "settings.json")
    updated = await shared_log.asave_settings({"blocklist_enabled": True})
    assert updated == {"blocklist_enabled": True}
    loaded = await shared_log.aload_settings()
    assert loaded == {"blocklist_enabled": True}


# ── 8. Link campaign history sorting & exact-page-size stop ──────────────────

@pytest.mark.asyncio
async def test_link_campaign_history_sorts_by_sent_at_and_stops_at_total(monkeypatch):
    from app.services import link_unsubscribe as svc

    svc.invalidate_campaign_history()
    now = datetime.now(timezone.utc)
    pages_requested = []

    class FakeClient:
        async def get_campaigns(self, page=1, per_page=100, **kw):
            pages_requested.append(page)
            return {
                "data": {
                    "results": [
                        {
                            "id": 1,
                            "name": "Created later, sent earlier",
                            "status": "finished",
                            "created_at": (now - timedelta(days=1)).isoformat(),
                            "started_at": (now - timedelta(days=5)).isoformat(),
                        },
                        {
                            "id": 2,
                            "name": "Created earlier, sent later",
                            "status": "finished",
                            "created_at": (now - timedelta(days=10)).isoformat(),
                            "started_at": (now - timedelta(days=2)).isoformat(),
                        },
                    ],
                    "total": 2,
                }
            }

    monkeypatch.setattr(svc, "CAMPAIGN_PAGE_SIZE", 2)
    history = await svc._fetch_campaign_history(FakeClient())
    assert [c["id"] for c in history] == [2, 1]
    assert pages_requested == [1], "must stop once total campaigns have been fetched"


# ── 9. Revocation cache expiry pruning & UTC-aware hard bounce timestamp ─────

def test_refresh_revoked_prunes_expired_in_memory_entries(tmp_path, monkeypatch):
    from app import auth

    rev_file = tmp_path / "revoked.json"
    rev_file.write_text("{}")
    monkeypatch.setattr(auth, "_revoked_path", lambda: rev_file)
    auth.invalidate_revocation_cache()
    auth._refresh_revoked()

    # Insert an already-expired nonce in memory without touching the file mtime.
    auth._revoked["expired-nonce"] = time.time() - 10
    auth._refresh_revoked()
    assert "expired-nonce" not in auth._revoked


@pytest.mark.asyncio
async def test_hard_bounce_cache_timestamp_is_utc_aware(monkeypatch):
    from app.services import hard_bounce_cache as hbc

    class FakeLM:
        _client = object()
        get_bounces = None

        async def paginate_all(self, fn, **kw):
            return []

    monkeypatch.setattr(hbc, "listmonk", FakeLM())
    await hbc.update_hard_bounce_counts()
    ts = datetime.fromisoformat(hbc.get_last_updated())
    assert ts.tzinfo is not None


# ── 10. Frontend regression checks ───────────────────────────────────────────

def test_unsubscribes_scan_and_reset_honour_error_contract():
    src = (REPO_ROOT / "static" / "js" / "unsubscribes.js").read_text()
    scan_section = src.split("async triggerScan()")[1].split("async toggleBlocklist")[0]
    reset_section = src.split("async resetAll()")[1]
    assert "!err?.handled" in scan_section
    assert "!err?.handled" in reset_section


def test_campaigns_update_preserves_content_type_and_validates():
    src = (REPO_ROOT / "static" / "js" / "campaigns.js").read_text()
    update_section = src.split("async update(id)")[1].split("async toggleArchive")[0]
    assert "content_type" in update_section
    assert "Select at least one list" in update_section
    assert "Dashboard.destroyCharts()" in src
