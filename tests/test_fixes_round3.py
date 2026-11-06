"""Regression tests for the fixes from the third full-codebase review.

Covers: auto-unblock reversing deliberate blocklists, RFC 3464 DSN parsing,
the upstream-401 redirect loop, draft-campaign attribution, link-scanner
re-processing after the processed TTL, bounce page totals, keyword false
positives, IMAP seen-marking of unrelated mail, poison-message keying, CSV
formula injection, export truncation, sender spoofing, HTML-only replies,
empty send windows, session revocation, import overwrite/modes, opener-fetch
cancellation, and the CI test gate.
"""

import asyncio
import email
import email.message
import email.policy
import json
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    from app.main import app
    with patch("app.main.verify_session", return_value=True):
        yield TestClient(app)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime.now(timezone.utc)


def _use_tmp_state(monkeypatch, tmp_path):
    import app.services.unsubscribe_log as shared_log
    monkeypatch.setattr(shared_log, "LOG_FILE", tmp_path / "log.json")
    monkeypatch.setattr(shared_log, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(shared_log, "PROCESSED_FILE", tmp_path / "processed.json")
    return shared_log


# ── 1. Auto-unblock must not reverse deliberate blocklists ───────────────────

def test_auto_unblock_query_requires_a_bounce_and_excludes_complaints():
    from app.services.auto_unblock import QUERY_BLOCKLISTED_ENGAGED as q
    assert "FROM bounces" in q and "type IN ('hard', 'soft')" in q
    assert "NOT IN (SELECT DISTINCT subscriber_id FROM bounces WHERE type = 'complaint')" in q


@pytest.mark.asyncio
async def test_auto_unblock_skips_unsubscribed_subscribers(tmp_path, monkeypatch):
    shared_log = _use_tmp_state(monkeypatch, tmp_path)
    from app.services import auto_unblock

    # One in the processed set (even past the IMAP TTL), one only in the log.
    old = time.time() - (shared_log._PROCESSED_TTL_SECONDS + 86400)
    (tmp_path / "processed.json").write_text(json.dumps(
        {"version": 1, "processed": {"unsub@x.com": old}}
    ))
    shared_log.save_log([{"email": "Logged@X.com", "source": "link"}])

    class Client:
        async def paginate_all(self, fn, **kw):
            return [{"id": 1, "email": "unsub@x.com"},
                    {"id": 2, "email": "logged@x.com"},
                    {"id": 3, "email": "bounced@x.com"}]
        get_subscribers = None

    subs = await auto_unblock.find_blocklisted_engaged(Client())
    assert [s["email"] for s in subs] == ["bounced@x.com"]


# ── 2. RFC 3464 DSNs are parsed ──────────────────────────────────────────────

_DSN = b"""From: MAILER-DAEMON@mx.example.net
To: bounces@sender.com
X-Original-To: bounces@sender.com
Subject: Undelivered Mail Returned to Sender
Date: Mon, 2 Nov 2026 10:00:00 +0000
Message-ID: <dsn-1@mx.example.net>
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="BB"

--BB
Content-Type: text/plain; charset=us-ascii

Your message could not be delivered to one or more recipients.

--BB
Content-Type: message/delivery-status

Reporting-MTA: dns; mx.example.net

Final-Recipient: rfc822; gone.user@example.org
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 Recipient address rejected

--BB
Content-Type: message/rfc822

From: Newsletter <news@sender.com>
To: gone.user@example.org
Subject: Our November news
X-Listmonk-Campaign: camp-uuid-1

Questions? Write to <support@sender.com>. Mailbox full of ideas!
--BB--
"""


def _parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


def test_dsn_recipient_comes_from_final_recipient(monkeypatch):
    from app.services import bounce_ingest
    monkeypatch.setattr(bounce_ingest.settings, "bounce_imap_user", "bounces@sender.com")
    msg = _parse(_DSN)
    body = bounce_ingest._extract_body(msg)
    assert bounce_ingest._extract_bounced_recipient(msg, body) == "gone.user@example.org"


def test_dsn_status_drives_classification_and_original_is_ignored():
    from app.services import bounce_ingest
    msg = _parse(_DSN)
    body = bounce_ingest._extract_body(msg)
    assert "Status: 5.1.1" in body
    # Our own newsletter text must not leak into classification input.
    assert "Mailbox full of ideas" not in body
    assert "support@sender.com" not in body
    result = bounce_ingest.classify_bounce(body, msg["Subject"])
    assert result["type"] == "hard"
    assert result["smtp_code"] == "5.1.1"


def test_dsn_campaign_hint_still_read_from_original():
    from app.services import bounce_ingest
    assert bounce_ingest._extract_campaign_hint(_parse(_DSN)) == "camp-uuid-1"


def test_x_original_to_is_not_taken_as_the_bounced_recipient(monkeypatch):
    from app.services import bounce_ingest
    monkeypatch.setattr(bounce_ingest.settings, "bounce_imap_user", "")
    raw = (b"From: MAILER-DAEMON@x\nX-Original-To: bounces@sender.com\n"
           b"Subject: failure\n\nDelivery to <real.person@example.org> failed\n")
    msg = _parse(raw)
    body = bounce_ingest._extract_body(msg)
    assert bounce_ingest._extract_bounced_recipient(msg, body) == "real.person@example.org"


# ── 3. Upstream 401 must not trigger the login redirect loop ─────────────────

def test_listmonk_401_maps_to_502(client):
    import httpx
    req = httpx.Request("GET", "http://lm:9000/api/campaigns")
    resp = httpx.Response(401, json={"message": "invalid API credentials"}, request=req)
    with patch("app.routers.campaigns.listmonk.get_campaigns", new_callable=AsyncMock,
               side_effect=httpx.HTTPStatusError("401", request=req, response=resp)):
        r = client.get("/api/campaigns?page=1&per_page=10")
    assert r.status_code == 502
    assert "LISTMONK_API_KEY" in r.json()["detail"]


# ── 4. Drafts / scheduled campaigns never receive attribution ────────────────

_SENT = {"id": 1, "name": "Sent", "status": "finished", "lists": [{"id": 7}],
         "created_at": _iso(NOW - timedelta(days=10)),
         "started_at": _iso(NOW - timedelta(days=9))}
_DRAFT = {"id": 2, "name": "Draft", "status": "draft", "lists": [{"id": 7}],
          "created_at": _iso(NOW - timedelta(days=1)), "started_at": None}
_SCHEDULED = {"id": 3, "name": "Sched", "status": "scheduled", "lists": [{"id": 7}],
              "created_at": _iso(NOW - timedelta(hours=5)), "started_at": None}


def test_link_attribution_skips_drafts():
    from app.services.link_unsubscribe import _pick_campaign_for_list_ids
    pick = _pick_campaign_for_list_ids([_SCHEDULED, _DRAFT, _SENT], {7})
    assert pick["campaign_id"] == 1


def test_imap_attribution_skips_drafts():
    from app.services.imap_unsubscribe import _match_campaign
    match = _match_campaign([_SCHEDULED, _DRAFT, _SENT], NOW, {7})
    assert match["campaign_id"] == 1


def test_bounce_attribution_skips_drafts():
    from app.services.bounce_ingest import _pick_campaign
    msg = _parse(b"Subject: x\n\nbody\n")
    camp = _pick_campaign(msg, {7}, [_SCHEDULED, _DRAFT, _SENT], NOW)
    assert camp["id"] == 1


def test_attribution_uses_send_date_not_creation_date():
    """An older draft sent after a newer campaign is the most recent send."""
    from app.services.link_unsubscribe import _pick_campaign_for_list_ids
    created_first_sent_last = {
        "id": 5, "name": "Late", "status": "finished", "lists": [{"id": 7}],
        "created_at": _iso(NOW - timedelta(days=30)),
        "started_at": _iso(NOW - timedelta(days=1)),
    }
    assert _pick_campaign_for_list_ids([_SENT, created_first_sent_last], {7})["campaign_id"] == 5


# ── 5. Link scanner must not re-log after the processed TTL ──────────────────

def _link_client(sub_lists):
    from app.services.listmonk_client import ListMonkClient

    calls = {"modify": 0}

    async def mock_request(method, path, **kwargs):
        if method == "GET" and path == "/api/lists":
            return {"data": {"results": [{"id": 1, "name": "N"}], "total": 1}}
        if method == "GET" and path == "/api/subscribers":
            return {"data": {"results": [{
                "id": 5, "email": "old@example.com", "name": "", "lists": sub_lists,
            }], "total": 1}}
        if method == "GET" and "/api/campaigns" in path:
            return {"data": {"results": [], "total": 0}}
        if method == "PUT" and path == "/api/subscribers/lists":
            calls["modify"] += 1
        return {"data": {}}

    client = ListMonkClient.__new__(ListMonkClient)
    client._client = None
    client._request = mock_request
    return client, calls


@pytest.mark.asyncio
async def test_link_scan_does_not_relog_after_ttl_expiry(tmp_path, monkeypatch):
    shared_log = _use_tmp_state(monkeypatch, tmp_path)
    from app.services import link_unsubscribe as svc

    handled = time.time() - (shared_log._PROCESSED_TTL_SECONDS + 86400)
    (tmp_path / "processed.json").write_text(json.dumps(
        {"version": 1, "processed": {"old@example.com": handled}}
    ))
    # Unsubscribed long ago (before we handled them) and never since.
    unsub_at = datetime.fromtimestamp(handled - 60, tz=timezone.utc).isoformat()
    client, calls = _link_client([{"id": 1, "subscription_status": "unsubscribed",
                                   "subscription_updated_at": unsub_at}])

    result = await svc.scan_link_unsubscribes(client)
    assert result["processed"] == 0
    assert calls["modify"] == 0
    assert shared_log.load_log() == []


@pytest.mark.asyncio
async def test_link_scan_handles_a_genuinely_new_unsubscribe(tmp_path, monkeypatch):
    shared_log = _use_tmp_state(monkeypatch, tmp_path)
    from app.services import link_unsubscribe as svc

    handled = time.time() - 30 * 86400
    (tmp_path / "processed.json").write_text(json.dumps(
        {"version": 1, "processed": {"old@example.com": handled}}
    ))
    # Re-subscribed and unsubscribed again yesterday.
    client, _ = _link_client([{"id": 1, "subscription_status": "unsubscribed",
                               "subscription_updated_at": _iso(NOW - timedelta(days=1))}])

    result = await svc.scan_link_unsubscribes(client)
    assert result["processed"] == 1
    # Handled-at must move forward, or the next scan would re-process again.
    assert shared_log.load_processed_map_all()["old@example.com"] > handled + 86400


# ── 6. Bounce page totals ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bounce_total_not_truncated_when_walk_stops_early():
    from app.services.bounce_list import fetch_filtered_bounces_page

    rows = [{"id": i, "email": f"u{i}@x.com", "type": "soft", "campaign": None}
            for i in range(250)]

    class C:
        async def get_bounces(self, page, per_page, campaign_id=None, source="", bounce_type=""):
            s = (page - 1) * per_page
            return {"data": {"results": rows[s:s + per_page], "total": 250}}

    res = await fetch_filtered_bounces_page(C(), page=2, per_page=100)
    assert res["data"]["total"] == 250, "last page would be unreachable in the UI"


@pytest.mark.asyncio
async def test_iter_pages_survives_server_side_per_page_cap():
    from app.services.listmonk_client import ListMonkClient

    rows = list(range(120))

    async def capped(page, per_page, **kw):   # server ignores per_page > 50
        s = (page - 1) * 50
        return {"data": {"results": rows[s:s + 50], "total": len(rows)}}

    got = await ListMonkClient().paginate_all(capped, per_page=100)
    assert got == rows


# ── 7. Unsubscribe keyword false positives ───────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Please remove me from this list.", True),
    ("UNSUBSCRIBE ME", True),
    ("exclude me, thanks", True),
    ("Can you remove meat options from the menu?", False),
    ("We should exclude meetings on Friday", False),
    ("Remove members from the group", False),
    ("Please do NOT remove me", False),
    ("don't unsubscribe me", False),
    ("I never said remove me... actually yes, remove me", True),
])
def test_keyword_matching(text, expected):
    from app.services.imap_unsubscribe import _find_unsubscribe_keyword
    assert bool(_find_unsubscribe_keyword(text)) is expected


# ── 8. Unrelated inbox mail stays unread ─────────────────────────────────────

class _PeekIMAP:
    def __init__(self, raws: dict):
        self.raws = raws
        self.fetch_specs: list = []
        self.stored: list = []

    def select(self, mailbox):
        return ("OK", [b"1"])

    def search(self, charset, criteria):
        return ("OK", [b" ".join(self.raws)])

    def fetch(self, msg_id, spec):
        self.fetch_specs.append((msg_id, spec))
        return ("OK", [(msg_id + b" (BODY[] {1})", self.raws[msg_id]), b")"])

    def store(self, msg_id, op, flags):
        self.stored.append(msg_id)
        return ("OK", [b""])

    def logout(self):
        return ("BYE", [b""])


def _reply(sender, text, mid, **headers):
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = "inbox@example.com"
    msg["Subject"] = "Re: Newsletter"
    msg["Message-ID"] = mid
    msg["Date"] = "Mon, 01 Sep 2025 12:00:00 +0000"
    for k, v in headers.items():
        msg[k.replace("_", "-")] = v
    msg.set_content(text)
    return msg.as_bytes()


def _imap_lm_client():
    from app.services.listmonk_client import ListMonkClient

    async def mock_request(method, path, **kwargs):
        if method == "GET" and "/api/campaigns" in path:
            return {"data": {"results": [{
                "id": 10, "name": "September", "status": "finished",
                "created_at": "2025-09-01", "lists": [{"id": 1}],
            }], "total": 1}}
        if method == "GET" and "/api/subscribers" in path:
            q = kwargs.get("params", {}).get("query", "")
            email_addr = q.split("'")[1] if "'" in q else "x@example.com"
            return {"data": {"results": [{
                "id": 5, "email": email_addr, "name": "U", "lists": [{"id": 1}],
            }], "total": 1}}
        return {"data": {}}

    client = ListMonkClient.__new__(ListMonkClient)
    client._client = None
    client._request = mock_request
    return client


@pytest.mark.asyncio
async def test_non_matching_mail_is_left_unread_and_skipped_next_time(tmp_path, monkeypatch):
    _use_tmp_state(monkeypatch, tmp_path)
    from app.services import imap_unsubscribe as svc

    conn = _PeekIMAP({
        b"1": _reply("a@example.com", "Thanks, great newsletter!", "<keep-unread@x>"),
        b"2": _reply("b@example.com", "Please remove me.", "<unsub@x>"),
    })
    monkeypatch.setattr(svc, "connect_imap", lambda: conn)

    first = await svc.scan_and_unsubscribe(_imap_lm_client())
    assert first["processed"] == 1
    assert conn.stored == [b"2"], "only the handled request may be marked \\Seen"
    assert all("PEEK" in spec for _, spec in conn.fetch_specs), "RFC822 fetch sets \\Seen implicitly"

    conn.fetch_specs.clear()
    conn.raws.pop(b"2")
    await svc.scan_and_unsubscribe(_imap_lm_client())
    # Known non-matching message: header peek only, no full body download.
    assert conn.fetch_specs == [(b"1", "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")]


@pytest.mark.asyncio
async def test_forged_sender_is_not_unsubscribed(tmp_path, monkeypatch):
    shared_log = _use_tmp_state(monkeypatch, tmp_path)
    from app.services import imap_unsubscribe as svc

    conn = _PeekIMAP({b"1": _reply(
        "victim@example.com", "remove me", "<forged@x>",
        Authentication_Results="mx.example.com; spf=fail; dkim=none; dmarc=fail",
    )})
    monkeypatch.setattr(svc, "connect_imap", lambda: conn)

    result = await svc.scan_and_unsubscribe(_imap_lm_client())
    assert result["processed"] == 0
    assert conn.stored == []
    assert shared_log.load_log() == []


def test_sender_parsed_from_address_not_display_name():
    from app.services.imap_unsubscribe import _extract_sender_email
    msg = _parse(b'From: "victim@corp.com" <mallory@evil.com>\n\nhi\n')
    assert _extract_sender_email(msg) == "mallory@evil.com"


def test_html_only_multipart_reply_is_read_and_quote_stripped():
    from app.services.imap_unsubscribe import _extract_body, _extract_reply_only, _find_unsubscribe_keyword
    msg = email.message.EmailMessage()
    msg["From"] = "a@example.com"
    msg.set_content("<div>Please remove me</div>", subtype="html")
    msg.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename="sig.png")
    body = _extract_body(msg)
    assert _find_unsubscribe_keyword(_extract_reply_only(body))

    quoted = email.message.EmailMessage()
    quoted.set_content('<p>Thanks!</p><blockquote>Reply "remove me" to leave</blockquote>', subtype="html")
    quoted.add_attachment(b"x", maintype="image", subtype="png", filename="a.png")
    assert not _find_unsubscribe_keyword(_extract_reply_only(_extract_body(quoted)))


# ── 9. Poison-message counter ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opener_check_outage_is_not_counted_as_poison(monkeypatch):
    import httpx
    from app.services import bounce_ingest

    raw = _DSN.replace(b"X-Original-To: bounces@sender.com\n", b"")
    stored = []

    class Conn:
        def select(self, *a, **k): return ("OK", [b"1"])
        def search(self, *a): return ("OK", [b"1"])
        def fetch(self, mid, spec): return ("OK", [(b"1 (RFC822)", raw)])
        def store(self, mid, op, flags): stored.append(mid)
        def logout(self): return ("BYE", [])

    class LM:
        async def get_campaigns(self, page=1, **kw):
            if page > 1:
                return {"data": {"results": []}}
            return {"data": {"results": [{"id": 1, "uuid": "camp-uuid-1", "status": "finished",
                                          "created_at": "2026-10-01", "lists": [{"id": 7}]}]}}

        async def get_subscribers(self, *a, **k):
            return {"data": {"results": [{"id": 9, "email": "gone.user@example.org",
                                          "lists": [{"id": 7}]}]}}

    monkeypatch.setattr(bounce_ingest, "connect_bounce_imap_rw", lambda: Conn())
    monkeypatch.setattr(bounce_ingest, "subscriber_opened_campaign", AsyncMock(
        side_effect=httpx.ConnectError("listmonk down")))

    for _ in range(bounce_ingest.POISON_MESSAGE_MAX_ATTEMPTS + 1):
        result = await bounce_ingest.ingest_bounce_mailbox(LM())
        assert result["errors"] == 1
    assert stored == [], "a valid bounce was consumed during a ListMonk outage"
    assert bounce_ingest._message_failures == {}


# ── 10. CSV formula injection ────────────────────────────────────────────────

def test_csv_export_neutralises_formulas():
    from app.services.export_service import dict_list_to_csv
    rows = [{"name": "=HYPERLINK(\"http://evil\")", "email": "+1@x.com", "n": -5, "ok": "Alice"}]
    out = "".join(dict_list_to_csv(rows, ["name", "email", "n", "ok"]))
    line = out.splitlines()[1]
    assert line.startswith("\"'=HYPERLINK")
    assert ",'+1@x.com," in line
    assert line.endswith(",-5,Alice"), "numbers and plain text must be untouched"


# ── 11. Export truncation ────────────────────────────────────────────────────

def test_subscriber_export_cap_is_far_above_real_lists():
    from app.routers import subscribers
    assert subscribers.EXPORT_PAGE_SIZE * subscribers.EXPORT_MAX_PAGES >= 1_000_000


# ── Empty send window ────────────────────────────────────────────────────────

def test_scheduler_rejects_identical_start_and_end(client, tmp_path, monkeypatch):
    from app.services import campaign_scheduler
    monkeypatch.setattr(campaign_scheduler, "SCHEDULE_FILE", tmp_path / "schedule.json")
    r = client.put("/api/scheduler", json={"start_hour": 0, "start_minute": 0,
                                          "end_hour": 0, "end_minute": 0})
    assert r.status_code == 400
    assert not (tmp_path / "schedule.json").exists()


@pytest.mark.asyncio
async def test_scheduler_tick_skips_empty_window(tmp_path, monkeypatch):
    from app.services import campaign_scheduler
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"enabled": True, "start_hour": 9, "start_minute": 0,
                                "end_hour": 9, "end_minute": 0,
                                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}))
    monkeypatch.setattr(campaign_scheduler, "SCHEDULE_FILE", path)
    lm = AsyncMock()
    await campaign_scheduler.run_scheduler_tick(lm)
    lm.change_campaign_status.assert_not_called()
    lm.paginate_all.assert_not_called()


# ── Session revocation ───────────────────────────────────────────────────────

def _login(c):
    r = c.post("/auth/login", json={"username": "admin", "password": "pw"})
    assert r.status_code == 200
    return r.cookies.get("lmpro_session")


# NOTE: TestClient is deliberately NOT used as a context manager here — that
# would run the app lifespan and start the real background loops (IMAP scans,
# auto-unblock) against whatever ListMonk/IMAP the local .env points at.

def test_logout_revokes_the_session_token(monkeypatch, tmp_path):
    from app.main import app
    from app.services import campaign_scheduler
    monkeypatch.setattr(campaign_scheduler, "SCHEDULE_FILE", tmp_path / "schedule.json")
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "pw")
    c = TestClient(app)
    token = _login(c)
    assert c.get("/api/scheduler").status_code == 200
    c.post("/auth/logout", follow_redirects=False)

    attacker = TestClient(app)   # replay the stolen cookie
    attacker.cookies.set("lmpro_session", token)
    assert attacker.get("/api/scheduler").status_code == 401


def test_password_change_invalidates_existing_sessions(monkeypatch, tmp_path):
    from app.main import app
    from app.services import campaign_scheduler
    monkeypatch.setattr(campaign_scheduler, "SCHEDULE_FILE", tmp_path / "schedule.json")
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "pw")
    c = TestClient(app)
    _login(c)
    assert c.get("/api/scheduler").status_code == 200
    monkeypatch.setenv("DASHBOARD_PASS", "rotated")
    assert c.get("/api/scheduler").status_code == 401


def test_legacy_two_part_token_is_rejected():
    from starlette.requests import Request
    from app import auth
    ts = str(int(time.time()))
    token = f"{ts}:{auth._sign(ts)}"
    req = Request({"type": "http", "method": "GET", "path": "/",
                   "headers": [(b"cookie", f"lmpro_session={token}".encode())]})
    assert auth.verify_session(req) is False


# ── Import overwrite / modes ─────────────────────────────────────────────────

def test_import_overwrite_is_opt_in_and_blocklist_mode_accepted(client):
    seen = {}

    async def fake_import(content, filename, params):
        seen.update(params)
        return {"data": {"status": "importing"}}

    with patch("app.routers.converter.listmonk.import_subscribers", side_effect=fake_import):
        r = client.post(
            "/api/converter/convert-and-import",
            files={"file": ("t.csv", BytesIO(b"email\na@b.c\n"), "text/csv")},
            data={"email_column": "email", "list_ids": "[1]", "mode": "blocklist"},
        )
    assert r.status_code == 200
    assert seen["mode"] == "blocklist"
    assert seen["overwrite"] is False


# ── Opener fetch survives an awaiter being cancelled ─────────────────────────

@pytest.mark.asyncio
async def test_cancelled_awaiter_does_not_cancel_shared_opener_fetch():
    from app.services import opener_cache

    gate = asyncio.Event()

    class Client:
        async def paginate_all(self, fn, **kw):
            await gate.wait()
            return [{"email": "A@x.com"}]
        get_subscribers = None

    c = Client()
    first = asyncio.create_task(opener_cache.get_opener_emails(c, 42))
    second = asyncio.create_task(opener_cache.get_opener_emails(c, 42))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)
    gate.set()
    assert await second == {"a@x.com"}
    assert opener_cache._inflight == {}


# ── UI copy / CI gate ────────────────────────────────────────────────────────

def test_status_toast_uses_proper_past_tense():
    js = (REPO_ROOT / "static" / "js" / "campaigns.js").read_text()
    assert "${action}ed" not in js


def test_deploy_requires_tests_to_pass():
    wf = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert "needs: test" in wf
    assert "pytest" in wf
