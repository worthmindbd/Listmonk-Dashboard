from datetime import datetime, time
from zoneinfo import ZoneInfo
import pytest
from unittest.mock import patch

from app.services.campaign_scheduler import is_within_send_window, save_schedule, load_schedule


def test_daytime_window():
    schedule = {
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 9,
        "start_minute": 0,
        "end_hour": 17,
        "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
    }

    # Monday 12:00 UTC -> inside
    dt_mon_noon = datetime(2026, 3, 9, 12, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_mon_noon
        assert is_within_send_window(schedule) is True

    # Monday 18:00 UTC -> outside
    dt_mon_evening = datetime(2026, 3, 9, 18, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_mon_evening
        assert is_within_send_window(schedule) is False

    # Sunday 12:00 UTC -> outside (Sunday not in days)
    dt_sun_noon = datetime(2026, 3, 8, 12, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_sun_noon
        assert is_within_send_window(schedule) is False


def test_overnight_window():
    schedule = {
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 21,
        "start_minute": 0,
        "end_hour": 5,
        "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
    }

    # Friday 22:00 UTC -> inside (Friday is active)
    dt_fri_night = datetime(2026, 3, 13, 22, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_fri_night
        assert is_within_send_window(schedule) is True

    # Saturday 03:00 UTC -> inside (started Friday night which was active!)
    dt_sat_early = datetime(2026, 3, 14, 3, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_sat_early
        assert is_within_send_window(schedule) is True

    # Saturday 06:00 UTC -> outside
    dt_sat_morning = datetime(2026, 3, 14, 6, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_sat_morning
        assert is_within_send_window(schedule) is False

    # Sunday 03:00 UTC -> outside (started Saturday night, which was NOT active)
    dt_sun_early = datetime(2026, 3, 15, 3, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_sun_early
        assert is_within_send_window(schedule) is False

    # Monday 03:00 UTC -> outside (started Sunday night, which was NOT active)
    dt_mon_early = datetime(2026, 3, 16, 3, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.campaign_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = dt_mon_early
        assert is_within_send_window(schedule) is False


def test_atomic_save_schedule(tmp_path, monkeypatch):
    import app.services.campaign_scheduler as sched_module
    sched_file = tmp_path / "schedule.json"
    monkeypatch.setattr(sched_module, "SCHEDULE_FILE", sched_file)

    data = {
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 10,
        "start_minute": 15,
        "end_hour": 18,
        "end_minute": 45,
        "days": ["mon"],
        "auto_paused_campaigns": [123],
    }
    save_schedule(data)
    loaded = load_schedule()
    assert loaded["enabled"] is True
    assert loaded["start_hour"] == 10
    assert loaded["auto_paused_campaigns"] == [123]


@pytest.mark.asyncio
async def test_update_schedule_endpoint_normalizes_days(tmp_path, monkeypatch):
    import app.services.campaign_scheduler as sched_module
    from app.main import update_schedule

    sched_file = tmp_path / "schedule.json"
    monkeypatch.setattr(sched_module, "SCHEDULE_FILE", sched_file)
    monkeypatch.setattr("app.main.load_schedule", sched_module.load_schedule)
    monkeypatch.setattr("app.main.save_schedule", sched_module.save_schedule)

    # Test that 3-letter abbreviations sent from frontend are accepted
    data = {
        "enabled": False,
        "days": ["mon", "WED", "Friday"],
    }
    result = await update_schedule(data)
    assert result["status"] == "ok"
    assert result["schedule"]["days"] == ["mon", "wed", "fri"]


class _FakeClock:
    """Helper to freeze campaign_scheduler.datetime.now(tz) at a chosen instant."""

    def __init__(self, current_dt: datetime):
        self.current_dt = current_dt

    def now(self, tz=None):
        if tz is not None:
            return self.current_dt.astimezone(tz)
        return self.current_dt

    def __getattr__(self, name):
        return getattr(datetime, name)


class _FakeListMonk:
    """Minimal stateful ListMonk campaign client for scheduler lifecycle tests."""

    def __init__(self, campaigns: list[dict]):
        self.campaigns = {c["id"]: dict(c) for c in campaigns}
        self.status_calls: list[tuple[int, str]] = []

    async def get_campaigns(self, *args, **kwargs):
        return {}

    async def paginate_all(self, fn, per_page=100, status="", **kwargs):
        return [dict(c) for c in self.campaigns.values() if c["status"] == status]

    async def change_campaign_status(self, cid: int, status: str):
        self.status_calls.append((cid, status))
        camp = self.campaigns[cid]
        camp["status"] = status
        return {"data": dict(camp)}

    async def delete_campaign(self, cid: int):
        self.campaigns.pop(cid, None)
        return {"data": True}


@pytest.mark.asyncio
async def test_manual_pause_via_dashboard_resumes_from_next_window_until_finished(tmp_path, monkeypatch):
    import app.services.campaign_scheduler as sched
    from app.routers import campaigns as campaigns_router

    sched_file = tmp_path / "schedule.json"
    monkeypatch.setattr(sched, "SCHEDULE_FILE", sched_file)
    sched.save_schedule({
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 8,
        "start_minute": 0,
        "end_hour": 20,
        "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "enabled_at": "2026-11-16T07:00:00+00:00",
    })

    clock = _FakeClock(datetime(2026, 11, 16, 14, 0, tzinfo=ZoneInfo("UTC")))  # Mon 14:00 (inside)
    monkeypatch.setattr(sched, "datetime", clock)

    lm = _FakeListMonk([
        {"id": 10, "name": "Promo", "status": "running", "updated_at": "2026-11-16T09:00:00+00:00"},
    ])
    monkeypatch.setattr(campaigns_router, "listmonk", lm)

    # 1. User manually pauses campaign #10 via dashboard mid-window (Mon 14:00)
    await campaigns_router.change_campaign_status(10, {"status": "paused"})
    saved = sched.load_schedule()
    assert "10" in saved["manual_paused_campaigns"]
    assert saved["manual_paused_campaigns"]["10"]["ready"] is False

    # 2. Tick later in the SAME window (Mon 16:00) -> stays paused
    clock.current_dt = datetime(2026, 11, 16, 16, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[10]["status"] == "paused"
    assert lm.status_calls == [(10, "paused")]

    # 3. Tick outside window (Mon 21:00) -> marks ready for next window, stays paused
    clock.current_dt = datetime(2026, 11, 16, 21, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[10]["status"] == "paused"
    assert sched.load_schedule()["manual_paused_campaigns"]["10"]["ready"] is True

    # 4. Next send window opens (Tue 08:05) -> auto-resumes!
    clock.current_dt = datetime(2026, 11, 17, 8, 5, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[10]["status"] == "running"
    assert lm.status_calls[-1] == (10, "running")
    saved = sched.load_schedule()
    assert saved["manual_paused_campaigns"] == {}
    assert 10 in saved["known_running_campaigns"]

    # 5. Window closes (Tue 20:05) -> auto-pauses until Wed window
    clock.current_dt = datetime(2026, 11, 17, 20, 5, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[10]["status"] == "paused"
    assert 10 in sched.load_schedule()["auto_paused_campaigns"]

    # 6. Wed window opens (Wed 08:05) -> auto-resumes
    clock.current_dt = datetime(2026, 11, 18, 8, 5, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[10]["status"] == "running"

    # 7. Campaign finishes -> scheduler cleans up tracking and leaves it alone
    lm.campaigns[10]["status"] = "finished"
    clock.current_dt = datetime(2026, 11, 18, 20, 5, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    saved = sched.load_schedule()
    assert saved["auto_paused_campaigns"] == []
    assert saved["manual_paused_campaigns"] == {}
    assert saved["known_running_campaigns"] == []


@pytest.mark.asyncio
async def test_manual_pause_outside_window_resumes_on_next_window(tmp_path, monkeypatch):
    import app.services.campaign_scheduler as sched
    from app.routers import campaigns as campaigns_router

    sched_file = tmp_path / "schedule.json"
    monkeypatch.setattr(sched, "SCHEDULE_FILE", sched_file)
    sched.save_schedule({
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 8,
        "start_minute": 0,
        "end_hour": 20,
        "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "enabled_at": "2026-11-16T07:00:00+00:00",
    })

    # Mon 22:00 (outside window)
    clock = _FakeClock(datetime(2026, 11, 16, 22, 0, tzinfo=ZoneInfo("UTC")))
    monkeypatch.setattr(sched, "datetime", clock)

    lm = _FakeListMonk([
        {"id": 11, "name": "Evening", "status": "running", "updated_at": "2026-11-16T21:55:00+00:00"},
    ])
    monkeypatch.setattr(campaigns_router, "listmonk", lm)

    await campaigns_router.change_campaign_status(11, {"status": "paused"})
    assert sched.load_schedule()["manual_paused_campaigns"]["11"]["ready"] is True

    # Still outside window at Mon 23:00 -> stays paused
    clock.current_dt = datetime(2026, 11, 16, 23, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[11]["status"] == "paused"

    # Tue 08:00 (next window opens) -> resumes!
    clock.current_dt = datetime(2026, 11, 17, 8, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[11]["status"] == "running"


@pytest.mark.asyncio
async def test_manual_pause_directly_in_listmonk_detected_and_resumed_next_window(tmp_path, monkeypatch):
    import app.services.campaign_scheduler as sched

    sched_file = tmp_path / "schedule.json"
    monkeypatch.setattr(sched, "SCHEDULE_FILE", sched_file)
    sched.save_schedule({
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 8,
        "start_minute": 0,
        "end_hour": 20,
        "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "enabled_at": "2026-11-16T07:00:00+00:00",
    })

    clock = _FakeClock(datetime(2026, 11, 16, 10, 0, tzinfo=ZoneInfo("UTC")))  # Mon 10:00
    monkeypatch.setattr(sched, "datetime", clock)

    lm = _FakeListMonk([
        {"id": 12, "name": "Direct", "status": "running", "updated_at": "2026-11-16T09:00:00+00:00"},
    ])

    # Tick 1: scheduler sees #12 running
    await sched.run_scheduler_tick(lm)
    assert 12 in sched.load_schedule()["known_running_campaigns"]

    # User pauses #12 directly in ListMonk UI at Mon 12:00
    lm.campaigns[12]["status"] = "paused"
    clock.current_dt = datetime(2026, 11, 16, 12, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[12]["status"] == "paused"
    assert sched.load_schedule()["manual_paused_campaigns"]["12"]["ready"] is False

    # Server missed overnight ticks and next tick runs on Tue 09:00 (next window!)
    clock.current_dt = datetime(2026, 11, 17, 9, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[12]["status"] == "running"


@pytest.mark.asyncio
async def test_manual_pause_overnight_window_does_not_resume_after_midnight(tmp_path, monkeypatch):
    import app.services.campaign_scheduler as sched
    from app.routers import campaigns as campaigns_router

    sched_file = tmp_path / "schedule.json"
    monkeypatch.setattr(sched, "SCHEDULE_FILE", sched_file)
    sched.save_schedule({
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 21,
        "start_minute": 0,
        "end_hour": 5,
        "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "enabled_at": "2026-11-13T20:00:00+00:00",
    })

    # Friday 22:30 UTC (inside Friday night overnight window)
    clock = _FakeClock(datetime(2026, 11, 13, 22, 30, tzinfo=ZoneInfo("UTC")))
    monkeypatch.setattr(sched, "datetime", clock)

    lm = _FakeListMonk([
        {"id": 13, "name": "Night", "status": "running"},
    ])
    monkeypatch.setattr(campaigns_router, "listmonk", lm)

    await campaigns_router.change_campaign_status(13, {"status": "paused"})

    # Saturday 02:30 UTC (still the SAME Friday overnight window!) -> must stay paused
    clock.current_dt = datetime(2026, 11, 14, 2, 30, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[13]["status"] == "paused"

    # Saturday 06:00 UTC (window closed) -> marks ready
    clock.current_dt = datetime(2026, 11, 14, 6, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[13]["status"] == "paused"
    assert sched.load_schedule()["manual_paused_campaigns"]["13"]["ready"] is True

    # Monday 21:15 UTC (next overnight window) -> resumes!
    clock.current_dt = datetime(2026, 11, 16, 21, 15, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[13]["status"] == "running"


@pytest.mark.asyncio
async def test_manual_resume_cancel_delete_and_preexisting_paused_campaign(tmp_path, monkeypatch):
    import app.services.campaign_scheduler as sched
    from app.routers import campaigns as campaigns_router

    sched_file = tmp_path / "schedule.json"
    monkeypatch.setattr(sched, "SCHEDULE_FILE", sched_file)
    sched.save_schedule({
        "enabled": True,
        "timezone": "UTC",
        "start_hour": 8,
        "start_minute": 0,
        "end_hour": 20,
        "end_minute": 0,
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "enabled_at": "2026-11-16T07:00:00+00:00",
    })

    clock = _FakeClock(datetime(2026, 11, 16, 10, 0, tzinfo=ZoneInfo("UTC")))
    monkeypatch.setattr(sched, "datetime", clock)

    lm = _FakeListMonk([
        # Campaign 20 was paused months BEFORE scheduler was enabled -> must never be touched
        {"id": 20, "name": "OldPaused", "status": "paused", "started_at": "2026-01-01T10:00:00+00:00"},
        {"id": 21, "name": "Active1", "status": "running"},
        {"id": 22, "name": "Active2", "status": "running"},
    ])
    monkeypatch.setattr(campaigns_router, "listmonk", lm)

    # Pause both 21 and 22
    await campaigns_router.change_campaign_status(21, {"status": "paused"})
    await campaigns_router.change_campaign_status(22, {"status": "paused"})
    assert set(sched.load_schedule()["manual_paused_campaigns"]) == {"21", "22"}

    # Cancel 21 and delete 22 -> both cleared from manual_paused_campaigns
    await campaigns_router.change_campaign_status(21, {"status": "cancelled"})
    await campaigns_router.delete_campaign(22)
    assert sched.load_schedule()["manual_paused_campaigns"] == {}

    # Next day tick -> old paused campaign #20 stays paused and is never resumed
    clock.current_dt = datetime(2026, 11, 17, 9, 0, tzinfo=ZoneInfo("UTC"))
    await sched.run_scheduler_tick(lm)
    assert lm.campaigns[20]["status"] == "paused"
    assert (20, "running") not in lm.status_calls
