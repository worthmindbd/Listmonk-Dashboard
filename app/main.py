import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pathlib import Path

from app.services.listmonk_client import listmonk
from app.services.auto_unblock import find_blocklisted_clickers, unblock_subscribers, QUERY_BLOCKLISTED_CLICKERS
from app.services.campaign_scheduler import (
    load_schedule, save_schedule, is_within_send_window,
    scheduler_loop, run_scheduler_tick,
)
from app.services.imap_unsubscribe import scan_and_unsubscribe
from app.services.link_unsubscribe import scan_link_unsubscribes
from app.services.bounce_ingest import ingest_bounce_mailbox
from app.auth import verify_session, create_session, clear_session, check_credentials
from app.routers import subscribers, lists, campaigns, templates, bounces, converter, unsubscribes

logger = logging.getLogger("listmonk-dashboard")
BASE_DIR = Path(__file__).resolve().parent.parent

AUTO_UNBLOCK_INTERVAL = 6 * 60 * 60
IMAP_SCAN_INTERVAL = 60 * 60  # 1 hour
BOUNCE_INGEST_INTERVAL = 60 * 60  # 1 hour
_auto_unblock_task = None
_scheduler_task = None
_imap_scan_task = None
_bounce_ingest_task = None


# ── Auth Middleware ───────────────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    """Protect all routes except login and static files."""

    OPEN_PATHS = {"/auth/login", "/auth/logout"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow static files, login page, and auth endpoints
        if path.startswith("/static") or path in self.OPEN_PATHS:
            return await call_next(request)

        # Check session
        if not verify_session(request):
            # API calls get 401, browser gets redirect
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
            return RedirectResponse("/auth/login", status_code=302)

        return await call_next(request)


# ── Background Tasks ─────────────────────────────────────

async def auto_unblock_loop():
    while True:
        try:
            subs = await find_blocklisted_clickers(listmonk)
            if subs:
                result = await unblock_subscribers(listmonk, subs)
                logger.info(f"Auto-unblock: {result['success']} unblocked, {result['failed']} failed")
        except Exception as e:
            logger.error(f"Auto-unblock error: {e}")
        await asyncio.sleep(AUTO_UNBLOCK_INTERVAL)


async def imap_scan_loop():
    """Scan IMAP inbox and ListMonk link unsubscribes every hour, starting immediately on startup."""
    while True:
        try:
            imap_result = await scan_and_unsubscribe(listmonk)
            if imap_result.get("processed", 0) > 0:
                logger.info(f"IMAP scan: {imap_result['processed']} unsubscribe(s) processed")
        except Exception as e:
            logger.error(f"IMAP scan error: {e}")
        try:
            link_result = await scan_link_unsubscribes(listmonk)
            if link_result.get("processed", 0) > 0:
                logger.info(f"Link scan: {link_result['processed']} unsubscribe(s) processed")
        except Exception as e:
            logger.error(f"Link unsubscribe scan error: {e}")
        await asyncio.sleep(IMAP_SCAN_INTERVAL)


async def bounce_ingest_loop():
    """Ingest new bounces from the IMAP mailbox into ListMonk every hour."""
    while True:
        try:
            result = await ingest_bounce_mailbox(listmonk)
            if result.get("ingested", 0) > 0:
                logger.info(
                    f"Bounce ingest: {result['ingested']} ingested "
                    f"(hard={result.get('hard', 0)}, soft={result.get('soft', 0)})"
                )
        except Exception as e:
            logger.error(f"Bounce ingest error: {e}")
        await asyncio.sleep(BOUNCE_INGEST_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _auto_unblock_task, _scheduler_task, _imap_scan_task, _bounce_ingest_task
    await listmonk.start()
    _auto_unblock_task = asyncio.create_task(auto_unblock_loop())
    _scheduler_task = asyncio.create_task(scheduler_loop(listmonk))
    _imap_scan_task = asyncio.create_task(imap_scan_loop())
    _bounce_ingest_task = asyncio.create_task(bounce_ingest_loop())
    logger.info("Background tasks started: auto-unblock (6h), campaign scheduler (60s), IMAP+link scan (1h), bounce ingest (1h)")
    yield
    _auto_unblock_task.cancel()
    _scheduler_task.cancel()
    _imap_scan_task.cancel()
    _bounce_ingest_task.cancel()
    await listmonk.close()


# ── App Setup ────────────────────────────────────────────

app = FastAPI(title="ListMonk Dashboard", lifespan=lifespan)
app.add_middleware(AuthMiddleware)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
jinja_templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Include routers
app.include_router(subscribers.router, prefix="/api/subscribers", tags=["Subscribers"])
app.include_router(lists.router, prefix="/api/lists", tags=["Lists"])
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["Campaigns"])
app.include_router(templates.router, prefix="/api/templates", tags=["Templates"])
app.include_router(bounces.router, prefix="/api/bounces", tags=["Bounces"])
app.include_router(converter.router, prefix="/api/converter", tags=["CSV Converter"])
app.include_router(unsubscribes.router, prefix="/api/unsubscribes", tags=["Unsubscribes"])


# ── Auth Routes ──────────────────────────────────────────

@app.get("/auth/login")
async def login_page(request: Request):
    if verify_session(request):
        return RedirectResponse("/", status_code=302)
    return jinja_templates.TemplateResponse("login.html", {"request": request})


@app.post("/auth/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")

    if not check_credentials(username, password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    response = JSONResponse({"status": "ok"})
    create_session(response)
    return response


@app.get("/auth/logout")
async def logout():
    response = RedirectResponse("/auth/login", status_code=302)
    clear_session(response)
    return response


# ── Dashboard ────────────────────────────────────────────

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
    schedule = load_schedule()
    for key in ["enabled", "timezone", "start_hour", "start_minute",
                "end_hour", "end_minute", "days"]:
        if key in data:
            schedule[key] = data[key]
    save_schedule(schedule)
    if data.get("enabled"):
        await run_scheduler_tick(listmonk)
    return {"status": "ok", "schedule": schedule}


@app.post("/api/scheduler/run")
async def scheduler_run_now():
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


