"""
Campaign Scheduler: Auto-pause/resume campaigns based on send time windows.

Checks every 60 seconds:
- If current time is OUTSIDE the send window and campaign is running -> pause it
- If current time is INSIDE the send window and campaign was auto-paused -> resume it

Tracks which campaigns were auto-paused so it doesn't interfere with manual pauses.
"""

import asyncio
import json
import logging
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.listmonk_client import ListMonkClient

logger = logging.getLogger("campaign_scheduler")

SCHEDULE_FILE = Path(__file__).resolve().parent.parent.parent / "schedule.json"

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
}


def load_schedule() -> dict:
    """Load schedule from JSON file."""
    if SCHEDULE_FILE.exists():
        try:
            with open(SCHEDULE_FILE) as f:
                data = json.load(f)
            # Merge with defaults for any missing keys
            merged = {**DEFAULT_SCHEDULE, **data}
            return merged
        except Exception as e:
            logger.error(f"Failed to load schedule: {e}")
    return dict(DEFAULT_SCHEDULE)


def save_schedule(schedule: dict):
    """Save schedule to JSON file."""
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(schedule, f, indent=2)


def is_within_send_window(schedule: dict) -> bool:
    """Check if current time is within the allowed send window."""
    tz = ZoneInfo(schedule["timezone"])
    now = datetime.now(tz)

    # Check day of week
    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    allowed_days = {day_map[d] for d in schedule.get("days", []) if d in day_map}
    if now.weekday() not in allowed_days:
        return False

    # Check time window
    start = time(schedule["start_hour"], schedule["start_minute"])
    end = time(schedule["end_hour"], schedule["end_minute"])
    current = now.time()

    if start <= end:
        return start <= current <= end
    else:
        # Overnight window (e.g., 20:00 to 08:00)
        return current >= start or current <= end


async def run_scheduler_tick(client: ListMonkClient):
    """Single tick of the scheduler. Called every 60 seconds."""
    schedule = load_schedule()

    if not schedule.get("enabled"):
        return

    in_window = is_within_send_window(schedule)
    auto_paused = set(schedule.get("auto_paused_campaigns", []))

    tz = ZoneInfo(schedule["timezone"])
    now = datetime.now(tz)
    logger.debug(f"Scheduler tick: {now.strftime('%A %H:%M %Z')} | in_window={in_window} | auto_paused={auto_paused}")

    try:
        # Get all campaigns that are running or paused
        result = await client.get_campaigns(1, 100)
        campaigns = result.get("data", {}).get("results", [])

        changed = False

        for camp in campaigns:
            cid = camp["id"]
            status = camp["status"]

            if not in_window and status == "running":
                # Outside send window -> pause it
                try:
                    await client.change_campaign_status(cid, "paused")
                    auto_paused.add(cid)
                    changed = True
                    logger.info(f"Auto-PAUSED campaign #{cid} '{camp['name']}' (outside {schedule['start_hour']:02d}:{schedule['start_minute']:02d}-{schedule['end_hour']:02d}:{schedule['end_minute']:02d} {schedule['timezone']})")
                except Exception as e:
                    logger.error(f"Failed to pause campaign #{cid}: {e}")

            elif in_window and status == "paused" and cid in auto_paused:
                # Inside send window + was auto-paused -> resume it
                try:
                    await client.change_campaign_status(cid, "running")
                    auto_paused.discard(cid)
                    changed = True
                    logger.info(f"Auto-RESUMED campaign #{cid} '{camp['name']}' (inside send window)")
                except Exception as e:
                    logger.error(f"Failed to resume campaign #{cid}: {e}")

        # Clean up auto_paused list: remove campaigns that are no longer paused or don't exist
        active_ids = {c["id"] for c in campaigns}
        auto_paused = auto_paused & active_ids
        # Also remove any that are no longer in paused state
        for camp in campaigns:
            if camp["id"] in auto_paused and camp["status"] != "paused":
                auto_paused.discard(camp["id"])
                changed = True

        if changed:
            schedule["auto_paused_campaigns"] = list(auto_paused)
            save_schedule(schedule)

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
