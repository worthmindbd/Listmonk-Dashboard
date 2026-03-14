import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pathlib import Path

from app.services.listmonk_client import listmonk
from app.services.auto_unblock import find_blocklisted_clickers, unblock_subscribers, QUERY_BLOCKLISTED_CLICKERS
from app.services.campaign_scheduler import (
    load_schedule, save_schedule, is_within_send_window,
    scheduler_loop, run_scheduler_tick, DEFAULT_SCHEDULE,
)
from app.routers import subscribers, lists, campaigns, templates, bounces, converter

logger = logging.getLogger("listmonk-dashboard")
BASE_DIR = Path(__file__).resolve().parent.parent

AUTO_UNBLOCK_INTERVAL = 6 * 60 * 60
_auto_unblock_task = None
_scheduler_task = None


async def auto_unblock_loop():
    """Background task that periodically unblocks clickers who got blocklisted."""
    while True:
        try:
            subs = await find_blocklisted_clickers(listmonk)
            if subs:
                result = await unblock_subscribers(listmonk, subs)
                logger.info(f"Auto-unblock: {result['success']} unblocked, {result['failed']} failed")
        except Exception as e:
            logger.error(f"Auto-unblock error: {e}")
        await asyncio.sleep(AUTO_UNBLOCK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _auto_unblock_task, _scheduler_task
    await listmonk.start()
    _auto_unblock_task = asyncio.create_task(auto_unblock_loop())
    _scheduler_task = asyncio.create_task(scheduler_loop(listmonk))
    logger.info("Background tasks started: auto-unblock (6h), campaign scheduler (60s)")
    yield
    _auto_unblock_task.cancel()
    _scheduler_task.cancel()
    await listmonk.close()


app = FastAPI(title="ListMonk Dashboard", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
jinja_templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Include routers
app.include_router(subscribers.router, prefix="/api/subscribers", tags=["Subscribers"])
app.include_router(lists.router, prefix="/api/lists", tags=["Lists"])
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["Campaigns"])
app.include_router(templates.router, prefix="/api/templates", tags=["Templates"])
app.include_router(bounces.router, prefix="/api/bounces", tags=["Bounces"])
app.include_router(converter.router, prefix="/api/converter", tags=["CSV Converter"])


@app.get("/")
async def index(request: Request):
    return jinja_templates.TemplateResponse("index.html", {"request": request})


# ── Auto-Unblock Endpoints ───────────────────────────────

@app.get("/api/auto-unblock/status")
async def auto_unblock_status():
    try:
        result = await listmonk.get_subscribers(1, 1, QUERY_BLOCKLISTED_CLICKERS)
        total = result.get("data", {}).get("total", 0)
        return {"blocklisted_clickers": total, "interval_hours": AUTO_UNBLOCK_INTERVAL // 3600}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/auto-unblock/run")
async def auto_unblock_run_now():
    try:
        subs = await find_blocklisted_clickers(listmonk)
        if not subs:
            return {"success": 0, "failed": 0, "unblocked": [], "message": "No blocklisted clickers found"}
        return await unblock_subscribers(listmonk, subs)
    except Exception as e:
        return {"error": str(e)}


# ── Campaign Scheduler Endpoints ─────────────────────────

@app.get("/api/scheduler")
async def get_schedule():
    """Get current schedule config + live status."""
    schedule = load_schedule()
    tz = ZoneInfo(schedule["timezone"])
    now = datetime.now(tz)
    in_window = is_within_send_window(schedule) if schedule["enabled"] else None
    return {
        **schedule,
        "current_time": now.strftime("%A %I:%M %p %Z"),
        "in_send_window": in_window,
    }


@app.put("/api/scheduler")
async def update_schedule(data: dict):
    """Update schedule config."""
    schedule = load_schedule()

    # Update allowed fields
    for key in ["enabled", "timezone", "start_hour", "start_minute",
                "end_hour", "end_minute", "days"]:
        if key in data:
            schedule[key] = data[key]

    save_schedule(schedule)

    # If just enabled, run a tick immediately
    if data.get("enabled"):
        await run_scheduler_tick(listmonk)

    return {"status": "ok", "schedule": schedule}


@app.post("/api/scheduler/run")
async def scheduler_run_now():
    """Manually trigger a scheduler tick."""
    try:
        await run_scheduler_tick(listmonk)
        schedule = load_schedule()
        return {
            "status": "ok",
            "in_send_window": is_within_send_window(schedule),
            "auto_paused_campaigns": schedule.get("auto_paused_campaigns", []),
        }
    except Exception as e:
        return {"error": str(e)}
