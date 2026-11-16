"""
Campaign Scheduler: Auto-pause/resume campaigns based on send time windows.

Checks every 60 seconds:
- If current time is OUTSIDE the send window and campaign is running -> pause it
- If current time is INSIDE the send window and campaign was auto-paused -> resume it
- If a campaign is manually paused -> keep it paused for the remainder of the
  current send window (if paused mid-window) and auto-resume it starting from
  the next send window until the campaign finishes
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.campaign_attribution import parse_listmonk_dt
from app.services.listmonk_client import ListMonkClient

logger = logging.getLogger("campaign_scheduler")

SCHEDULE_FILE = settings.data_path("schedule.json")

# Default schedule
DEFAULT_SCHEDULE = {
    "enabled": False,
    "timezone": "US/Eastern",
    "start_hour": 8,
    "start_minute": 0,
    "end_hour": 20,
    "end_minute": 0,
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "auto_paused_campaigns": [],
    # campaign id -> the campaign's updated_at immediately after we paused it.
    # A later timestamp means a human paused/changed it; defer resume until
    # the next send window.
    "auto_paused_updated_at": {},
    # str(campaign_id) -> {"window_start": iso|None, "window_end": iso|None, "ready": bool}
    # Tracks manually paused campaigns so they stay paused for the rest of
    # the current send window and auto-resume from the next send window.
    "manual_paused_campaigns": {},
    # Campaign ids observed as running while the scheduler is enabled so manual
    # pauses performed directly in ListMonk are detected on the next tick.
    "known_running_campaigns": [],
    # UTC ISO timestamp of when the scheduler was enabled.
    "enabled_at": None,
}


def load_schedule() -> dict:
    """Load schedule from JSON file."""
    if SCHEDULE_FILE.exists():
        try:
            with open(SCHEDULE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults for any missing keys
            merged = {**DEFAULT_SCHEDULE, **data}
            return merged
        except Exception as e:
            logger.error(f"Failed to load schedule: {e}")
    return dict(DEFAULT_SCHEDULE)


# Serialises every load→mutate→save cycle on schedule.json (the 60s tick, a
# manually triggered tick, and the PUT /api/scheduler endpoint). Without it a
# tick that spends minutes paginating campaigns can save its stale snapshot
# over the user's settings, and overlapping ticks can drop each other's
# auto_paused_campaigns entries — stranding campaigns in "paused" forever.
_SCHEDULE_SAVE_LOCK = asyncio.Lock()


def save_schedule(schedule: dict):
    """Save schedule to JSON file atomically."""
    try:
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_path = tempfile.mkstemp(
            dir=SCHEDULE_FILE.parent,
            prefix="schedule_",
            suffix=".tmp",
        )
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(schedule, f, indent=2)
        os.replace(temp_path, SCHEDULE_FILE)
    except OSError as e:
        logger.error(f"Failed to save schedule to {SCHEDULE_FILE}: {e}")
        raise


def _schedule_has_valid_window(schedule: dict) -> bool:
    day_map = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    if not day_map.intersection(schedule.get("days", [])):
        return False
    if (schedule.get("start_hour"), schedule.get("start_minute")) == (
        schedule.get("end_hour"), schedule.get("end_minute")
    ):
        return False
    return True


def is_within_send_window(schedule: dict) -> bool:
    """Check if current time is within the allowed send window."""
    tz = ZoneInfo(schedule["timezone"])
    now = datetime.now(tz)

    # Check day of week
    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    allowed_days = {day_map[d] for d in schedule.get("days", []) if d in day_map}

    # Check time window
    start = time(schedule["start_hour"], schedule["start_minute"])
    end = time(schedule["end_hour"], schedule["end_minute"])
    current = now.time()

    if start <= end:
        if now.weekday() not in allowed_days:
            return False
        return start <= current <= end
    else:
        # Overnight window (e.g., 20:00 to 08:00)
        # If current time is after start, the window began today.
        # If current time is before end, the window began yesterday!
        if current >= start:
            return now.weekday() in allowed_days
        elif current <= end:
            yesterday_weekday = (now.weekday() - 1) % 7
            return yesterday_weekday in allowed_days
        else:
            return False


def get_current_window_bounds(
    schedule: dict, now: datetime | None = None
) -> tuple[datetime, datetime] | None:
    """Return (window_start, window_end) in schedule's timezone when `now` is in a send window."""
    tz = ZoneInfo(schedule["timezone"])
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    allowed_days = {day_map[d] for d in schedule.get("days", []) if d in day_map}

    start = time(schedule["start_hour"], schedule["start_minute"])
    end = time(schedule["end_hour"], schedule["end_minute"])
    current = now.time()

    if start <= end:
        if now.weekday() not in allowed_days or not (start <= current <= end):
            return None
        return (
            datetime.combine(now.date(), start, tzinfo=tz),
            datetime.combine(now.date(), end, tzinfo=tz),
        )

    if current >= start and now.weekday() in allowed_days:
        return (
            datetime.combine(now.date(), start, tzinfo=tz),
            datetime.combine(now.date() + timedelta(days=1), end, tzinfo=tz),
        )
    if current <= end:
        yesterday = now.date() - timedelta(days=1)
        if yesterday.weekday() in allowed_days:
            return (
                datetime.combine(yesterday, start, tzinfo=tz),
                datetime.combine(now.date(), end, tzinfo=tz),
            )
    return None


def _current_window_info(
    schedule: dict, now: datetime, in_window: bool
) -> tuple[str | None, str | None]:
    if not in_window:
        return (None, None)
    bounds = get_current_window_bounds(schedule, now)
    if bounds is not None:
        win_start, win_end = bounds
        return (win_start.isoformat(), win_end.isoformat())
    # Fallback only when is_within_send_window was mocked in a test outside clock hours.
    return (f"date:{now.date().isoformat()}", None)


def _make_manual_pause_entry(schedule: dict, now: datetime, in_window: bool) -> dict:
    if not in_window:
        return {"window_start": None, "window_end": None, "ready": True}
    win_start, win_end = _current_window_info(schedule, now, True)
    return {"window_start": win_start, "window_end": win_end, "ready": False}


def _is_manual_pause_ready(
    entry: object, now: datetime, cur_win_start: str | None
) -> bool:
    if not isinstance(entry, dict):
        return True
    if entry.get("ready"):
        return True
    entry_start = entry.get("window_start")
    entry_end = entry.get("window_end")
    if entry_end and cur_win_start:
        try:
            end_dt = datetime.fromisoformat(str(entry_end))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=now.tzinfo)
            if now > end_dt and cur_win_start != entry_start:
                return True
        except (ValueError, TypeError):
            pass
    elif (
        entry_end is None
        and isinstance(entry_start, str)
        and entry_start.startswith("date:")
        and isinstance(cur_win_start, str)
        and cur_win_start.startswith("date:")
        and cur_win_start != entry_start
    ):
        return True
    return False


def _load_int_set(raw: object) -> set[int]:
    result: set[int] = set()
    if not isinstance(raw, list):
        return result
    for item in raw:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _load_meta_dict(raw: object) -> dict[int, object]:
    result: dict[int, object] = {}
    if not isinstance(raw, dict):
        return result
    for raw_cid, val in raw.items():
        try:
            result[int(raw_cid)] = val
        except (TypeError, ValueError):
            continue
    return result


def _load_manual_paused(raw: object) -> dict[int, dict]:
    result: dict[int, dict] = {}
    if not isinstance(raw, dict):
        return result
    for raw_cid, entry in raw.items():
        try:
            cid = int(raw_cid)
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict):
            result[cid] = {
                "window_start": entry.get("window_start"),
                "window_end": entry.get("window_end"),
                "ready": bool(entry.get("ready", False)),
            }
        else:
            result[cid] = {"window_start": None, "window_end": None, "ready": True}
    return result


def _apply_tracking_to_schedule(
    schedule: dict,
    auto_paused: set[int],
    auto_paused_meta: dict[int, object],
    manual_paused: dict[int, dict],
    known_running: set[int],
) -> None:
    schedule["auto_paused_campaigns"] = sorted(auto_paused)
    schedule["auto_paused_updated_at"] = {
        str(cid): ts for cid, ts in auto_paused_meta.items()
        if cid in auto_paused
    }
    schedule["manual_paused_campaigns"] = {
        str(cid): {
            "window_start": entry.get("window_start"),
            "window_end": entry.get("window_end"),
            "ready": bool(entry.get("ready", False)),
        }
        for cid, entry in sorted(manual_paused.items())
    }
    schedule["known_running_campaigns"] = sorted(known_running)


async def handle_manual_campaign_status_change(campaign_id: int, new_status: str) -> None:
    """Update scheduler tracking when a user changes campaign status via the dashboard.

    When the scheduler is enabled:
    - Pausing queues the campaign to auto-resume from the next send window
      (staying paused for the rest of the current window if paused mid-window).
    - Resuming, cancelling, or other transitions clear any pending pause state.
    """
    normalized = (new_status or "").strip().lower()
    async with _SCHEDULE_SAVE_LOCK:
        schedule = load_schedule()
        enabled = bool(schedule.get("enabled"))

        auto_paused = _load_int_set(schedule.get("auto_paused_campaigns"))
        auto_paused_meta = _load_meta_dict(schedule.get("auto_paused_updated_at"))
        manual_paused = _load_manual_paused(schedule.get("manual_paused_campaigns"))
        known_running = _load_int_set(schedule.get("known_running_campaigns"))

        changed = False

        if normalized == "paused":
            if not enabled:
                return
            if campaign_id in auto_paused:
                auto_paused.discard(campaign_id)
                auto_paused_meta.pop(campaign_id, None)
                changed = True
            if campaign_id in known_running:
                known_running.discard(campaign_id)
                changed = True

            try:
                tz = ZoneInfo(schedule["timezone"])
                now = datetime.now(tz)
                in_window = _schedule_has_valid_window(schedule) and is_within_send_window(schedule)
            except Exception:
                now = datetime.now(timezone.utc)
                in_window = False

            manual_paused[campaign_id] = _make_manual_pause_entry(schedule, now, in_window)
            changed = True
        else:
            if campaign_id in auto_paused:
                auto_paused.discard(campaign_id)
                auto_paused_meta.pop(campaign_id, None)
                changed = True
            if campaign_id in manual_paused:
                manual_paused.pop(campaign_id, None)
                changed = True
            if normalized == "running" and enabled:
                if campaign_id not in known_running:
                    known_running.add(campaign_id)
                    changed = True
            elif campaign_id in known_running:
                known_running.discard(campaign_id)
                changed = True

        if not changed:
            return

        _apply_tracking_to_schedule(
            schedule, auto_paused, auto_paused_meta, manual_paused, known_running,
        )
        try:
            save_schedule(schedule)
        except OSError as e:
            logger.error(f"Could not persist manual status update for campaign #{campaign_id}: {e}")


async def clear_campaign_tracking(campaign_id: int) -> None:
    """Remove all scheduler tracking for a deleted campaign."""
    async with _SCHEDULE_SAVE_LOCK:
        if not SCHEDULE_FILE.exists():
            return
        schedule = load_schedule()
        auto_paused = _load_int_set(schedule.get("auto_paused_campaigns"))
        auto_paused_meta = _load_meta_dict(schedule.get("auto_paused_updated_at"))
        manual_paused = _load_manual_paused(schedule.get("manual_paused_campaigns"))
        known_running = _load_int_set(schedule.get("known_running_campaigns"))

        changed = False
        if campaign_id in auto_paused:
            auto_paused.discard(campaign_id)
            auto_paused_meta.pop(campaign_id, None)
            changed = True
        if campaign_id in manual_paused:
            manual_paused.pop(campaign_id, None)
            changed = True
        if campaign_id in known_running:
            known_running.discard(campaign_id)
            changed = True

        if not changed:
            return

        _apply_tracking_to_schedule(
            schedule, auto_paused, auto_paused_meta, manual_paused, known_running,
        )
        try:
            save_schedule(schedule)
        except OSError as e:
            logger.error(f"Could not clear scheduler tracking for campaign #{campaign_id}: {e}")


async def run_scheduler_tick(client: ListMonkClient):
    """Single tick of the scheduler. Called every 60 seconds.

    The whole tick runs under ``_SCHEDULE_SAVE_LOCK`` so a manual run and the
    background loop cannot interleave their campaign status changes and saves,
    and so a PUT /api/scheduler cannot be clobbered mid-tick (it waits for the
    lock, then reads the fresh state).
    """
    async with _SCHEDULE_SAVE_LOCK:
        schedule = load_schedule()

        if not schedule.get("enabled"):
            return

        # A schedule with no usable days is never "in window", which would make
        # this tick pause every running campaign and never resume any of them.
        # The API rejects an empty day list, so this only guards a hand-edited or
        # corrupted schedule file — skip the tick rather than stall all sending.
        day_map = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        if not day_map.intersection(schedule.get("days", [])):
            logger.error(
                "Schedule has no valid days configured; skipping this tick so "
                "running campaigns are not paused indefinitely."
            )
            return

        # Same reasoning for an empty window (start == end): it is never "in
        # window", so ticking would pause everything forever. The API rejects
        # it; this guards a hand-edited file.
        if (schedule["start_hour"], schedule["start_minute"]) == (
            schedule["end_hour"], schedule["end_minute"]
        ):
            logger.error(
                "Schedule start and end times are identical (empty send window); "
                "skipping this tick so running campaigns are not paused indefinitely."
            )
            return

        tz = ZoneInfo(schedule["timezone"])
        now = datetime.now(tz)
        now_utc = datetime.now(timezone.utc)
        in_window = is_within_send_window(schedule)
        cur_win_start, _ = _current_window_info(schedule, now, in_window)

        auto_paused = _load_int_set(schedule.get("auto_paused_campaigns"))
        auto_paused_meta = _load_meta_dict(schedule.get("auto_paused_updated_at"))
        manual_paused = _load_manual_paused(schedule.get("manual_paused_campaigns"))
        known_running = _load_int_set(schedule.get("known_running_campaigns"))

        state_dirty = False
        if not schedule.get("enabled_at"):
            schedule["enabled_at"] = now_utc.isoformat()
            state_dirty = True
        enabled_at_dt = parse_listmonk_dt(schedule.get("enabled_at"))

        if not in_window:
            # Once outside any send window, every manually paused campaign is
            # ready to resume when the next send window opens.
            for entry in manual_paused.values():
                if not entry.get("ready") or entry.get("window_start") or entry.get("window_end"):
                    entry["ready"] = True
                    entry["window_start"] = None
                    entry["window_end"] = None
                    state_dirty = True

        def persist_state() -> None:
            """Persist scheduler ownership state immediately, not only at tick end.

            A pause must never be durable without its bookkeeping: a crash or
            save error after pausing used to leave a campaign paused forever
            with no record that the scheduler should resume it.
            """
            _apply_tracking_to_schedule(
                schedule, auto_paused, auto_paused_meta, manual_paused, known_running,
            )
            save_schedule(schedule)

        logger.debug(
            f"Scheduler tick: {now.strftime('%A %H:%M %Z')} | in_window={in_window} | "
            f"auto_paused={auto_paused} | manual_paused={set(manual_paused)}"
        )

        try:
            # Only running/paused campaigns are relevant: running ones may need
            # pausing, and paused ones may need resuming. This avoids paginating
            # the entire campaign history every 60 seconds.
            campaigns = []
            for status in ("running", "paused"):
                campaigns.extend(
                    await client.paginate_all(
                        client.get_campaigns, per_page=100, status=status,
                        no_body=True,
                    )
                )

            changed = False

            for camp in campaigns:
                cid = camp["id"]
                status = camp["status"]

                if status == "running":
                    if cid in manual_paused:
                        manual_paused.pop(cid, None)
                        state_dirty = True
                    if cid in auto_paused:
                        auto_paused.discard(cid)
                        auto_paused_meta.pop(cid, None)
                        state_dirty = True

                    if not in_window:
                        # Record ownership *before* pausing. If the process dies
                        # between the pause and the next save, the next tick sees
                        # a running campaign in the set and cleans it up.
                        was_known_running = cid in known_running
                        auto_paused.add(cid)
                        auto_paused_meta.pop(cid, None)
                        known_running.discard(cid)
                        try:
                            persist_state()
                        except OSError as e:
                            auto_paused.discard(cid)
                            if was_known_running:
                                known_running.add(cid)
                            logger.error(
                                f"Not pausing campaign #{cid}: could not persist "
                                f"auto_paused state: {e}"
                            )
                            continue
                        try:
                            result = await client.change_campaign_status(cid, "paused")
                        except Exception as e:
                            auto_paused.discard(cid)
                            auto_paused_meta.pop(cid, None)
                            known_running.add(cid)
                            try:
                                persist_state()
                            except OSError:
                                logger.exception("Could not persist auto-pause rollback")
                            logger.error(f"Failed to pause campaign #{cid}: {e}")
                        else:
                            updated_at = ((result or {}).get("data") or {}).get("updated_at")
                            auto_paused_meta[cid] = updated_at
                            try:
                                persist_state()
                            except OSError as e:
                                # The pause succeeded; keeping ownership in memory
                                # (the pre-pause save already stored the id) lets a
                                # later tick resume it instead of stranding it.
                                logger.error(
                                    f"Campaign #{cid} paused, but ownership metadata "
                                    f"could not be saved: {e}"
                                )
                            camp["status"] = "paused"
                            changed = True
                            logger.info(
                                f"Auto-PAUSED campaign #{cid} '{camp['name']}' "
                                f"(outside {schedule['start_hour']:02d}:{schedule['start_minute']:02d}-"
                                f"{schedule['end_hour']:02d}:{schedule['end_minute']:02d} {schedule['timezone']})"
                            )
                    else:
                        if cid not in known_running:
                            known_running.add(cid)
                            state_dirty = True

                elif status == "paused":
                    if cid in auto_paused:
                        if cid in manual_paused:
                            auto_paused.discard(cid)
                            auto_paused_meta.pop(cid, None)
                            state_dirty = True
                        else:
                            # If updated_at changed after we auto-paused it, a human
                            # paused/changed the campaign; defer auto-resume until
                            # the next send window.
                            known_updated = auto_paused_meta.get(cid)
                            current_updated = camp.get("updated_at")
                            known_dt = parse_listmonk_dt(known_updated)
                            current_dt = parse_listmonk_dt(current_updated)
                            manually_changed = (
                                known_dt is not None and current_dt is not None
                                and (current_dt - known_dt).total_seconds() > 5
                            )
                            if manually_changed:
                                auto_paused.discard(cid)
                                auto_paused_meta.pop(cid, None)
                                manual_paused[cid] = _make_manual_pause_entry(
                                    schedule, now, in_window
                                )
                                changed = True
                                try:
                                    persist_state()
                                except OSError:
                                    logger.exception("Could not persist manual-pause transition")
                                logger.info(
                                    f"Campaign #{cid} changed while auto-paused; "
                                    "scheduled to auto-resume from next send window"
                                )
                                continue
                            if in_window:
                                # Inside send window + was auto-paused -> resume it
                                try:
                                    await client.change_campaign_status(cid, "running")
                                    auto_paused.discard(cid)
                                    auto_paused_meta.pop(cid, None)
                                    known_running.add(cid)
                                    persist_state()
                                    camp["status"] = "running"
                                    changed = True
                                    logger.info(f"Auto-RESUMED campaign #{cid} '{camp['name']}' (inside send window)")
                                except Exception as e:
                                    logger.error(f"Failed to resume campaign #{cid}: {e}")
                            continue

                    if cid not in manual_paused:
                        started_dt = parse_listmonk_dt(camp.get("started_at"))
                        started_after_enable = (
                            started_dt is not None
                            and enabled_at_dt is not None
                            and started_dt >= enabled_at_dt
                        )
                        if cid in known_running or started_after_enable:
                            known_running.discard(cid)
                            manual_paused[cid] = _make_manual_pause_entry(
                                schedule, now, in_window
                            )
                            state_dirty = True
                            logger.info(
                                f"Detected manual pause for campaign #{cid} '{camp['name']}'; "
                                "scheduled to auto-resume from next send window"
                            )

                    if cid in manual_paused and in_window:
                        if _is_manual_pause_ready(manual_paused[cid], now, cur_win_start):
                            try:
                                await client.change_campaign_status(cid, "running")
                                manual_paused.pop(cid, None)
                                known_running.add(cid)
                                persist_state()
                                camp["status"] = "running"
                                changed = True
                                logger.info(
                                    f"Auto-RESUMED manually paused campaign #{cid} "
                                    f"'{camp['name']}' (next send window)"
                                )
                            except Exception as e:
                                logger.error(f"Failed to resume manually paused campaign #{cid}: {e}")

            # Clean up tracking lists: remove any that no longer match or no longer exist
            camp_map = {c["id"]: c for c in campaigns}
            for cid in list(auto_paused):
                camp = camp_map.get(cid)
                if not camp or camp["status"] != "paused":
                    auto_paused.discard(cid)
                    auto_paused_meta.pop(cid, None)
                    state_dirty = True

            for cid in list(manual_paused):
                camp = camp_map.get(cid)
                if not camp or camp["status"] != "paused":
                    manual_paused.pop(cid, None)
                    state_dirty = True

            for cid in list(known_running):
                camp = camp_map.get(cid)
                if not camp or camp["status"] != "running":
                    known_running.discard(cid)
                    state_dirty = True

            if state_dirty or changed:
                persist_state()

        except Exception as e:
            logger.error(f"Scheduler tick error: {e}")


async def scheduler_loop(client: ListMonkClient):
    """Background loop that runs the scheduler every 60 seconds."""
    while True:
        try:
            await run_scheduler_tick(client)
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
        await asyncio.sleep(60)
