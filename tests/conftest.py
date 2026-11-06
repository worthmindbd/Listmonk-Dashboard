"""Shared pytest fixtures.

Several services memoise state in module-level globals (campaign history,
opener sets, hard-bounce counts, the unsubscribe processed set). Those caches
are correct in production but leak between tests when one test populates them
and a later test asserts against a fresh mock. Resetting them per test keeps
ordering from silently changing results.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_module_caches(tmp_path, monkeypatch):
    from app import auth
    from app.auth import _login_failures
    from app.routers import campaigns
    from app.services import (
        bounce_ingest, hard_bounce_cache, imap_unsubscribe, link_unsubscribe, opener_cache,
    )

    # Session revocations persist to DATA_DIR; keep tests out of the real one.
    monkeypatch.setattr(auth, "_revoked_path", lambda: tmp_path / "revoked_sessions.json")

    def _reset():
        # Server-side session revocation set (logout).
        auth.invalidate_revocation_cache()
        # IMAP inbox messages already evaluated as non-matching.
        imap_unsubscribe.invalidate_non_matching_cache()
        link_unsubscribe.invalidate_campaign_history()
        opener_cache.invalidate()
        hard_bounce_cache._hard_bounce_counts = {}
        hard_bounce_cache._last_updated = ""
        # 60s summary cache in the campaigns router — a cached summary from one
        # test would satisfy another test's fresh mocks (AGENTS.md rule 11).
        campaigns.invalidate_summary_cache()
        # Poison-message retry counters for bounce ingestion.
        bounce_ingest._message_failures.clear()
        # Login rate-limit buckets — a failed-login test must not rate-limit
        # later tests for the same IP.
        _login_failures.clear()

    _reset()
    yield
    _reset()
