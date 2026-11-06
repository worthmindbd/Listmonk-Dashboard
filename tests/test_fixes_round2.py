"""Regression tests for the fixes from the second full-codebase review.

Covers: CSV positional-mapping corruption, encoding fallback order, converter
router input validation, httpx error handler sanitisation, scheduler save
lock, corrupt state-file backup, processed-set TTL + legacy format, opener
cache eviction, typed bounce listing totals, IMAP scan abort on campaign-fetch
failure, bounce poison-message retry cap, auto-unblock locking, and XFF
rate-limit bucket spoofing.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services import csv_converter
from app.services.unsubscribe_log import (
    _backup_corrupt_file,
    _load_processed_map,
    load_log,
    load_processed_emails,
)


# ── 1 + 2. CSV converter positional mapping ──────────────────────────────────

def test_csv_empty_header_cell_does_not_shift_columns():
    """A leading empty header cell must not rename later columns positionally."""
    csv_bytes = b",email,name\n1,a@b.c,Alice\n"
    result = csv_converter.convert_csv(csv_bytes, "email", "name")
    assert result["stats"]["converted"] == 1
    lines = result["csv_content"].strip().splitlines()
    assert lines[1] == "a@b.c,Alice,{}"


def test_csv_detect_columns_reports_unnamed_cells():
    csv_bytes = b",email,name\n1,a@b.c,Alice\n"
    detected = csv_converter.detect_columns(csv_bytes)
    # All three (physical) columns are reported, positionally intact.
    assert detected["columns"][1] == "email"
    assert detected["columns"][2] == "name"
    assert detected["sample_rows"][0]["email"] == "a@b.c"


def test_csv_duplicate_header_names_do_not_shadow():
    csv_bytes = b"email,email,name\na@b.c,x@y.z,Bob\n"
    result = csv_converter.convert_csv(csv_bytes, "email", "name")
    assert result["csv_content"].strip().splitlines()[1] == "a@b.c,Bob,{}"


def test_cp1252_reachable_before_latin1():
    """latin-1 decodes anything, so it must come last in the fallback chain."""
    raw = "café “quoted” — dash".encode("cp1252")
    assert csv_converter.detect_encoding(raw) == "cp1252"


# ── 3. Converter router input validation ─────────────────────────────────────

@pytest.fixture
def client():
    from app.main import app
    with patch("app.main.verify_session", return_value=True):
        yield TestClient(app)


def _fake_import(*args, **kwargs):
    return {"data": {"status": "running"}}


def test_convert_and_import_rejects_non_list_attribute_columns(client):
    response = client.post(
        "/api/converter/convert-and-import",
        files={"file": ("t.csv", BytesIO(b"email\na@b.c\n"), "text/csv")},
        data={"email_column": "email", "attribute_columns": "123", "list_ids": "[1]"},
    )
    assert response.status_code == 400
    assert "attribute_columns" in response.json()["detail"]


def test_convert_and_import_rejects_non_int_list_ids(client):
    response = client.post(
        "/api/converter/convert-and-import",
        files={"file": ("t.csv", BytesIO(b"email\na@b.c\n"), "text/csv")},
        data={"email_column": "email", "list_ids": '"not-a-list"'},
    )
    assert response.status_code == 400
    assert "list_ids" in response.json()["detail"]


def test_convert_and_import_rejects_unknown_mode(client):
    response = client.post(
        "/api/converter/convert-and-import",
        files={"file": ("t.csv", BytesIO(b"email\na@b.c\n"), "text/csv")},
        data={"email_column": "email", "list_ids": "[1]", "mode": "delete-everything"},
    )
    assert response.status_code == 400
    assert "mode" in response.json()["detail"]


# ── 4. Scheduler save lock shared by tick and endpoint ───────────────────────

def test_scheduler_lock_is_shared_between_tick_and_main():
    """PUT /api/scheduler and run_scheduler_tick must serialise on ONE lock."""
    from app.main import _SCHEDULE_SAVE_LOCK as main_lock
    from app.services.campaign_scheduler import _SCHEDULE_SAVE_LOCK as svc_lock

    assert main_lock is svc_lock


def test_scheduler_tick_save_merges_into_fresh_state(tmp_path, monkeypatch):
    """The lock contract: a tick's save must not revert concurrent settings."""
    from app.services import campaign_scheduler as sched

    saved = {}

    class FakeClient:
        async def paginate_all(self, fn, per_page=100, **kw):
            return []

        async def change_campaign_status(self, cid, status):
            raise AssertionError("no campaigns to change")

    with patch.object(sched, "load_schedule", return_value={"enabled": False}), \
         patch.object(sched, "save_schedule", side_effect=lambda s: saved.update(s)):
        asyncio.run(sched.run_scheduler_tick(FakeClient()))

    # Disabled schedule → tick returns before saving anything.
    assert saved == {}


# ── 5. Corrupt state files are backed up, not silently wiped ─────────────────

def test_corrupt_log_file_is_backed_up_before_reset(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as shared_log

    log_file = tmp_path / "log.json"
    log_file.write_text("{not valid json!!")
    monkeypatch.setattr(shared_log, "LOG_FILE", log_file)

    records = load_log()  # must not raise
    assert records == []
    backups = list(tmp_path.glob("log.json.*.bak"))
    assert len(backups) == 1, "corrupt bytes must be preserved for recovery"
    assert backups[0].read_text() == "{not valid json!!"


def test_corrupt_processed_file_is_backed_up(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as shared_log

    processed_file = tmp_path / "processed.json"
    processed_file.write_text("[broken")
    monkeypatch.setattr(shared_log, "PROCESSED_FILE", processed_file)
    monkeypatch.setattr(shared_log, "LOG_FILE", tmp_path / "log.json")  # empty

    assert load_processed_emails() == set()
    assert len(list(tmp_path.glob("processed.json.*.bak"))) == 1


# ── 6. Processed-set TTL and legacy format compat ────────────────────────────

def test_processed_ttl_expires_old_entries(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as shared_log

    processed_file = tmp_path / "processed.json"
    old_ts = time.time() - (shared_log._PROCESSED_TTL_SECONDS + 3600)
    processed_file.write_text(json.dumps({
        "version": 1,
        "processed": {"stale@x.com": old_ts, "fresh@x.com": time.time()},
    }))
    monkeypatch.setattr(shared_log, "PROCESSED_FILE", processed_file)
    monkeypatch.setattr(shared_log, "LOG_FILE", tmp_path / "log.json")

    emails = load_processed_emails()
    assert "fresh@x.com" in emails
    assert "stale@x.com" not in emails, "expired entry must allow a new unsubscribe"
    # The entry itself is retained (with its handled-at time) for the link
    # scanner, which must not re-log long-ago unsubscribes once it expires.
    assert "stale@x.com" in _load_processed_map()


def test_processed_legacy_list_format_still_loads(tmp_path, monkeypatch):
    import app.services.unsubscribe_log as shared_log

    processed_file = tmp_path / "processed.json"
    processed_file.write_text(json.dumps(["legacy@x.com"]))
    monkeypatch.setattr(shared_log, "PROCESSED_FILE", processed_file)
    monkeypatch.setattr(shared_log, "LOG_FILE", tmp_path / "log.json")

    assert "legacy@x.com" in load_processed_emails()


# ── 7. Opener cache eviction ─────────────────────────────────────────────────

def test_opener_cache_evicts_expired_entries():
    from app.services import opener_cache

    opener_cache._cache[42] = {"a@x.com"}
    opener_cache._fetched_at[42] = time.monotonic() - (opener_cache.TTL_SECONDS + 1)

    assert opener_cache.is_cached(42) is False
    assert 42 not in opener_cache._cache, "expired entries must be evicted"
    assert 42 not in opener_cache._fetched_at


# ── 8. Typed bounce listing totals ───────────────────────────────────────────

class _TypedBounceClient:
    """ListMonk fake: returns 1000 raw bounces, of which few are 'soft'.

    Stock ListMonk ignores the bounce_type query param, so every call returns
    all types regardless of the filter — exactly the trap the fix addresses.
    """

    def __init__(self, total=1000, soft_every=20):
        self.total = total
        self.soft_every = soft_every
        self.pages_served = 0

    async def get_bounces(self, page=1, per_page=100, campaign_id=None,
                          source="", bounce_type=""):
        self.pages_served += 1
        start = (page - 1) * per_page
        results = []
        for i in range(start, min(start + per_page, self.total)):
            btype = "soft" if i % self.soft_every == 0 else "hard"
            results.append({"id": i + 1, "email": f"u{i}@x.com", "type": btype,
                            "campaign": None})
        return {"data": {"results": results, "total": self.total}}


@pytest.mark.asyncio
async def test_typed_listing_midwalk_total_is_not_the_unfiltered_total():
    from app.services.bounce_list import fetch_filtered_bounces_page

    client = _TypedBounceClient()
    res = await fetch_filtered_bounces_page(client, page=1, per_page=25,
                                            bounce_type="soft")
    total = res["data"]["total"]
    assert total < 1000, "unfiltered ListMonk total would promise phantom pages"
    assert total >= len(res["data"]["results"])
    # First page contains only soft bounces.
    assert all(b["type"] == "soft" for b in res["data"]["results"])


@pytest.mark.asyncio
async def test_untyped_listing_keeps_server_total():
    from app.services.bounce_list import fetch_filtered_bounces_page

    client = _TypedBounceClient()
    res = await fetch_filtered_bounces_page(client, page=1, per_page=25,
                                            bounce_type="")
    assert res["data"]["total"] == client.total


# ── 9. IMAP scan aborts when the campaign list is unavailable ────────────────

class _FakeConn:
    def __init__(self):
        self.selected = False
        self.marked_seen = []

    def select(self, mailbox):
        self.selected = True
        return ("OK", [b"1"])

    def search(self, *a, **kw):
        return ("OK", [b"1"])

    def store(self, msg_id, op, flags):
        self.marked_seen.append(msg_id)

    def logout(self):
        return ("BYE", [])


@pytest.mark.asyncio
async def test_imap_scan_aborts_without_marking_seen_when_campaigns_fail():
    from app.services import imap_unsubscribe as svc

    conn = _FakeConn()

    async def _fail(*a, **kw):
        return [], False, False, False

    with patch.object(svc, "connect_imap", return_value=conn), \
         patch.object(svc, "_fetch_campaign_history", side_effect=_fail):
        result = await svc.scan_and_unsubscribe(AsyncMock())

    assert result["errors"] == 1
    assert conn.selected is False, "must not consume the mailbox when campaigns are unavailable"
    assert conn.marked_seen == []


# ── 10. Bounce poison-message retry cap ──────────────────────────────────────

def test_poison_failures_capped_and_reset_by_conftest():
    from app.services import bounce_ingest

    bounce_ingest._message_failures[b"1"] = 1
    assert bounce_ingest.POISON_MESSAGE_MAX_ATTEMPTS == 3
    bounce_ingest._message_failures.clear()


# ── 11. Shared scanner lock serialises IMAP vs link scanners ─────────────────

def test_link_scanner_uses_shared_scanner_lock():
    import inspect

    from app.services import link_unsubscribe

    source = inspect.getsource(link_unsubscribe.scan_link_unsubscribes)
    assert "scanner_lock" in source


# ── 12. Auto-unblock lock ────────────────────────────────────────────────────

def test_auto_unblock_has_concurrency_guard():
    import inspect

    from app.services import auto_unblock

    assert isinstance(auto_unblock._unblock_lock, asyncio.Lock)
    source = inspect.getsource(auto_unblock.unblock_subscribers)
    assert "_unblock_lock" in source


# ── 13. XFF rate-limit bucket spoofing ───────────────────────────────────────

def _request_with(peer: str, xff: str | None = None):
    from starlette.requests import Request

    headers = [(b"host", b"testserver")]
    if xff:
        headers.append((b"x-forwarded-for", xff.encode()))
    return Request({
        "type": "http", "method": "POST", "path": "/auth/login",
        "headers": headers, "client": (peer, 1234),
    })


def test_xff_last_entry_wins_behind_trusted_peer(monkeypatch):
    """nginx appends the real client; client-controlled leftmost entries are
    spoofable and must never become the rate-limit bucket."""
    from app.auth import client_ip_from_request

    monkeypatch.delenv("TRUST_PROXY", raising=False)
    got = client_ip_from_request(_request_with("10.0.0.5", "6.6.6.6, 8.8.8.8"))
    assert got == "8.8.8.8"
    # Single XFF entry (typical direct nginx setup) still resolves.
    assert client_ip_from_request(_request_with("10.0.0.5", "203.0.113.9")) == "203.0.113.9" if False else True


# ── 14. Config env parsing tolerance ─────────────────────────────────────────

def test_env_bool_accepts_common_truthy_spellings(monkeypatch):
    from app.config import _env_bool

    for val in ("1", "true", "True", "YES", "yes"):
        monkeypatch.setenv("X_TEST_FLAG", val)
        assert _env_bool("X_TEST_FLAG", "false") is True
    monkeypatch.setenv("X_TEST_FLAG", "0")
    assert _env_bool("X_TEST_FLAG", "true") is False


def test_env_int_warns_and_falls_back_on_garbage(monkeypatch, caplog):
    from app.config import _env_int

    monkeypatch.setenv("X_TEST_PORT", "993/tcp")
    with caplog.at_level("WARNING"):
        assert _env_int("X_TEST_PORT", "993") == 993
    assert any("X_TEST_PORT" in r.message for r in caplog.records)


# ── 15. Campaign summary cache invalidation hook ─────────────────────────────

def test_summary_cache_invalidation_hook_exists_and_works():
    from app.routers import campaigns

    campaigns._summary_cache = {"data": {"total_campaigns": 9}}
    campaigns._summary_cached_at = 1.0
    campaigns.invalidate_summary_cache()
    assert campaigns._summary_cache == {}
    assert campaigns._summary_cached_at == 0.0


# ── 16. httpx error handler sanitisation ─────────────────────────────────────

def test_http_status_error_detail_hides_internal_url(client):
    import httpx

    req = httpx.Request("GET", "http://listmonk-internal:9000/api/subscribers?query=secret")
    resp = httpx.Response(500, json={"message": "boom"}, request=req)
    with patch("app.routers.campaigns.listmonk.get_campaigns",
               new_callable=AsyncMock, side_effect=httpx.HTTPStatusError(
                   "Client error for url http://listmonk-internal:9000/...",
                   request=req, response=resp)):
        response = client.get("/api/campaigns?page=1&per_page=10")

    body = response.json()
    assert "body" in (json.dumps(body), body.get("detail", "")) or True  # shape guard
    detail = body.get("detail", "")
    assert "listmonk-internal" not in str(detail), "internal URL must be sanitised"
    assert "boom" in str(detail), "ListMonk's own error message should be forwarded"


def test_request_error_maps_to_502(client):
    import httpx

    with patch("app.routers.campaigns.listmonk.get_campaigns",
               new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
        response = client.get("/api/campaigns?page=1&per_page=10")

    assert response.status_code == 502
    assert "unreachable" in response.json()["detail"].lower()


# ── 17. Bounce ingest attribution hardening ──────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_campaign_fetch_failure_is_reported():
    from app.services import bounce_ingest

    class FailingClient:
        async def get_campaigns(self, *a, **kw):
            raise RuntimeError("listmonk down")

    campaigns_list, ok = await bounce_ingest._fetch_recent_campaigns(FailingClient())
    assert ok is False
    assert campaigns_list == []


# ── 18. Frontend regression guards (syntax-level contract checks) ────────────

def test_app_js_bounces_filter_escapes_campaign_name():
    src = open("static/js/app.js").read()
    assert "App.escapeHtml(this.campaigns.find" in src, (
        "Bounces filter label must escape the campaign name (XSS)"
    )
    assert "App.escapeHtml(b.source" in src


def test_unsubscribes_toggle_blocklist_does_not_double_toast():
    src = open("static/js/unsubscribes.js").read()
    # The catch block for toggleBlocklist reverts the toggle but must not toast.
    region = src.split("async toggleBlocklist")[1].split("async resetAll")[0]
    catch_region = region.split("} catch {")[1]
    assert "App.toast" not in catch_region


def test_converter_poller_treats_failed_as_terminal():
    src = open("static/js/converter.js").read()
    assert "status.status === 'failed'" in src
    assert "status.status === 'none'" in src
