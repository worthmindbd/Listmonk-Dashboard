"""Regression tests for Unsubscribe page fixes and enhancements."""

import json
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient


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


def test_stats_endpoint_handles_unknown_and_numeric_campaign_id(client, tmp_path, monkeypatch):
    """GET /api/unsubscribes/stats?campaign_id=unknown and ?campaign_id=10 must not raise 422."""
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
            "campaign_id": 10,
            "campaign_name": "Campaign Ten",
            "campaign_key": "2026-03",
            "timestamp": "2026-03-02T12:00:00Z",
        },
    ]
    state.save_log(records)

    # 1. Unknown campaign stats (string)
    r_unknown = client.get("/api/unsubscribes/stats?campaign_id=unknown")
    assert r_unknown.status_code == 200
    data_unknown = r_unknown.json()
    assert data_unknown["total"] == 2
    assert data_unknown["campaign_count"] == 1
    assert data_unknown["campaign_link_count"] == 0
    assert data_unknown["campaign_email_count"] == 1

    # 2. Numeric campaign stats (integer as string in query)
    r_ten = client.get("/api/unsubscribes/stats?campaign_id=10")
    assert r_ten.status_code == 200
    data_ten = r_ten.json()
    assert data_ten["campaign_count"] == 1
    assert data_ten["campaign_link_count"] == 1
    assert data_ten["campaign_email_count"] == 0


def test_campaign_records_search_query_and_source_filter(client, tmp_path, monkeypatch):
    """GET /api/unsubscribes/campaign/{id} supports query and source filtering."""
    state = _use_tmp_state(monkeypatch, tmp_path)
    records = [
        {
            "email": "alice@example.com",
            "name": "Alice Wonderland",
            "keyword": "remove me",
            "source": "email",
            "campaign_id": 5,
            "campaign_name": "Campaign Five",
            "timestamp": "2026-03-01T12:00:00Z",
        },
        {
            "email": "bob@example.com",
            "name": "Bob Builder",
            "keyword": None,
            "source": "link",
            "campaign_id": 5,
            "campaign_name": "Campaign Five",
            "timestamp": "2026-03-02T12:00:00Z",
        },
        {
            "email": "charlie@example.com",
            "name": "Charlie Chaplin",
            "keyword": "unsubscribe me",
            "source": "email",
            "campaign_id": 5,
            "campaign_name": "Campaign Five",
            "timestamp": "2026-03-03T12:00:00Z",
        },
    ]
    state.save_log(records)

    # 1. Search by name query
    r_query = client.get("/api/unsubscribes/campaign/5?query=alice")
    assert r_query.status_code == 200
    res_query = r_query.json()["data"]
    assert res_query["total"] == 1
    assert res_query["total_unfiltered"] == 3
    assert res_query["results"][0]["email"] == "alice@example.com"

    # 2. Search by keyword
    r_kw = client.get("/api/unsubscribes/campaign/5?query=unsubscribe")
    assert r_kw.status_code == 200
    assert r_kw.json()["data"]["total"] == 1
    assert r_kw.json()["data"]["results"][0]["email"] == "charlie@example.com"

    # 3. Filter by source=link
    r_link = client.get("/api/unsubscribes/campaign/5?source=link")
    assert r_link.status_code == 200
    assert r_link.json()["data"]["total"] == 1
    assert r_link.json()["data"]["results"][0]["email"] == "bob@example.com"

    # 4. Filter by source=email
    r_email = client.get("/api/unsubscribes/campaign/5?source=email")
    assert r_email.status_code == 200
    assert r_email.json()["data"]["total"] == 2


def test_records_delete_scoped_by_campaign_id(client, tmp_path, monkeypatch):
    """DELETE /api/unsubscribes/records?emails=...&campaign_id=... only deletes from that campaign."""
    state = _use_tmp_state(monkeypatch, tmp_path)
    records = [
        {
            "email": "shared@example.com",
            "campaign_id": 10,
            "timestamp": "2026-03-01T12:00:00Z",
        },
        {
            "email": "shared@example.com",
            "campaign_id": 20,
            "timestamp": "2026-03-02T12:00:00Z",
        },
        {
            "email": "other@example.com",
            "campaign_id": 10,
            "timestamp": "2026-03-03T12:00:00Z",
        },
    ]
    state.save_log(records)

    # Delete shared@example.com ONLY from campaign 10
    r = client.delete("/api/unsubscribes/records?emails=shared@example.com&campaign_id=10")
    assert r.status_code == 200
    assert r.json()["removed"] == 1

    remaining = state.load_log()
    assert len(remaining) == 2
    # Campaign 20 record for shared@example.com must still be there!
    c20_rec = [r for r in remaining if r["campaign_id"] == 20]
    assert len(c20_rec) == 1
    assert c20_rec[0]["email"] == "shared@example.com"


def test_campaign_groups_enrichment_and_sorting(client, tmp_path, monkeypatch):
    """GET /api/unsubscribes/campaigns updates blank campaign names and sorts unknown at the bottom."""
    state = _use_tmp_state(monkeypatch, tmp_path)
    records = [
        # Record with empty campaign name
        {
            "email": "u1@example.com",
            "campaign_id": 5,
            "campaign_name": "",
            "campaign_key": "2026-02",
            "timestamp": "2026-02-01T12:00:00Z",
        },
        # Later record with proper campaign name
        {
            "email": "u2@example.com",
            "campaign_id": 5,
            "campaign_name": "February Newsletter",
            "campaign_key": "2026-02",
            "timestamp": "2026-02-02T12:00:00Z",
        },
        # Newer campaign
        {
            "email": "u3@example.com",
            "campaign_id": 6,
            "campaign_name": "March Promo",
            "campaign_key": "2026-03",
            "timestamp": "2026-03-01T12:00:00Z",
        },
        # Unknown campaign
        {
            "email": "u4@example.com",
            "campaign_id": None,
            "campaign_name": "No matching campaign",
            "campaign_key": "unknown",
            "timestamp": "2026-03-05T12:00:00Z",
        },
    ]
    state.save_log(records)

    r = client.get("/api/unsubscribes/campaigns")
    assert r.status_code == 200
    groups = r.json()["data"]
    assert len(groups) == 3

    # Group 5 should have enriched campaign_name
    g5 = next(g for g in groups if g["campaign_id"] == 5)
    assert g5["campaign_name"] == "February Newsletter"
    assert g5["count"] == 2

    # Sorting order: March 2026 first, February 2026 second, unknown last
    assert groups[0]["campaign_key"] == "2026-03"
    assert groups[1]["campaign_key"] == "2026-02"
    assert groups[2]["campaign_key"] == "unknown"


def test_csv_exports_include_source_column(client, tmp_path, monkeypatch):
    """Both campaign export and all export must include the source column."""
    state = _use_tmp_state(monkeypatch, tmp_path)
    records = [
        {
            "email": "user1@example.com",
            "name": "User One",
            "source": "link",
            "campaign_id": 7,
            "campaign_name": "Test Campaign",
            "campaign_key": "2026-03",
            "timestamp": "2026-03-01T12:00:00Z",
        },
    ]
    state.save_log(records)

    # 1. Export all
    r_all = client.get("/api/unsubscribes/export")
    assert r_all.status_code == 200
    lines_all = r_all.text.splitlines()
    assert "source" in lines_all[0]
    assert "link" in lines_all[1]

    # 2. Export campaign
    r_camp = client.get("/api/unsubscribes/campaign/7/export")
    assert r_camp.status_code == 200
    lines_camp = r_camp.text.splitlines()
    assert "source" in lines_camp[0]
    assert "link" in lines_camp[1]


def test_load_log_non_list_or_non_dict_recovery(tmp_path, monkeypatch):
    """load_log handles corrupt non-list files and non-dict records gracefully."""
    import app.services.unsubscribe_log as shared_log
    log_file = tmp_path / "log.json"
    monkeypatch.setattr(shared_log, "LOG_FILE", log_file)

    # 1. Non-list root in JSON
    log_file.write_text(json.dumps({"invalid": "root"}), encoding="utf-8")
    assert shared_log.load_log() == []
    # Verify backup was created
    baks = list(tmp_path.glob("log.json.*.bak"))
    assert len(baks) >= 1

    # 2. List containing non-dict items
    log_file.write_text(json.dumps([None, 123, "test", {"email": "good@example.com"}]), encoding="utf-8")
    loaded = shared_log.load_log()
    assert len(loaded) == 1
    assert loaded[0]["email"] == "good@example.com"


def test_get_unsubscribes_global_query(client, tmp_path, monkeypatch):
    """GET /api/unsubscribes?query=... filters global log."""
    state = _use_tmp_state(monkeypatch, tmp_path)
    records = [
        {"email": "alpha@example.com", "name": "Alpha", "timestamp": "2026-03-01T12:00:00Z"},
        {"email": "beta@example.com", "name": "Beta", "timestamp": "2026-03-02T12:00:00Z"},
    ]
    state.save_log(records)

    r = client.get("/api/unsubscribes?query=alpha")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["results"][0]["email"] == "alpha@example.com"
