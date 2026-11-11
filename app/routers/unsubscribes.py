import asyncio
import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from app.services.imap_unsubscribe import (
    get_stats, check_imap_status, scan_and_unsubscribe,
)
from app.services.unsubscribe_log import (
    aload_log, asave_log, load_settings, save_settings,
    aload_settings, asave_settings,
    unmark_processed, _log_lock,
)
from app.services.link_unsubscribe import scan_link_unsubscribes
from app.services.listmonk_client import listmonk
from app.services.export_service import dict_list_to_csv
from app.services.task_utils import spawn

router = APIRouter()

# Pagination bounds. These lists are served from an in-memory JSON log, so the
# cap exists to stop a single request from materialising an unbounded slice.
MAX_PAGE = 1000
MAX_PER_PAGE = 200

# "Undo all" touches every record in the log, and each one costs 2-3 serial
# ListMonk round trips. Left unbounded a large log holds the worker for hours
# and the client times out long before anything is restored.
RESET_MAX_RECORDS = 5000
RESET_CONCURRENCY = 10

# Long reset/scan operations run in the background so a reverse proxy timeout
# cannot make the UI report failure while the server is still working. A single
# slot each; concurrent requests get the current status.
_reset_job: dict | None = None
_scan_job: dict | None = None


@router.get("/settings")
async def get_unsub_settings():
    """Return current unsubscribe scanner settings."""
    return await aload_settings()


# Whitelist of persisted settings keys — arbitrary keys otherwise accumulate
# in unsubscribe_settings.json forever (and are silently merged on every read).
_ALLOWED_SETTINGS_KEYS = {"blocklist_enabled"}


@router.put("/settings")
async def update_unsub_settings(request: Request):
    """Update unsubscribe scanner settings (e.g., blocklist toggle)."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Settings must be a JSON object")
    unknown = set(data) - _ALLOWED_SETTINGS_KEYS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown setting(s): {', '.join(sorted(unknown))}. Allowed: {', '.join(sorted(_ALLOWED_SETTINGS_KEYS))}",
        )
    if "blocklist_enabled" in data and not isinstance(data["blocklist_enabled"], bool):
        raise HTTPException(status_code=400, detail="blocklist_enabled must be a boolean")
    return await asave_settings(data)


@router.get("")
async def get_unsubscribes(page: int = Query(1, ge=1, le=MAX_PAGE),
                           per_page: int = Query(25, ge=1, le=MAX_PER_PAGE),
                           query: str = Query("", max_length=100)):
    """Return paginated unsubscribe records (newest first)."""
    records = await aload_log()
    if query:
        q = query.lower().strip()
        records = [
            r for r in records
            if q in (r.get("email") or "").lower()
            or q in (r.get("name") or "").lower()
            or q in (r.get("campaign_name") or "").lower()
        ]
    records.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
    total = len(records)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": {
            "results": records[start:end],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    }


@router.get("/stats")
async def get_unsubscribe_stats(campaign_id: str = ""):
    """Return aggregate unsubscribe counts. Optionally filter by campaign_id."""
    stats = await asyncio.to_thread(get_stats)
    if campaign_id:
        records = await aload_log()
        campaign_records = [r for r in records if _matches_campaign_id(r.get("campaign_id"), campaign_id)]
        stats["campaign_count"] = len(campaign_records)
        stats["campaign_link_count"] = sum(1 for r in campaign_records if r.get("source") == "link")
        stats["campaign_email_count"] = len(campaign_records) - stats["campaign_link_count"]
    return stats


@router.get("/imap-status")
async def get_imap_status():
    """Check if IMAP is configured and can connect."""
    return await check_imap_status()


@router.get("/campaigns")
async def get_campaign_groups():
    """Return unsubscribe records grouped by individual campaign (campaign_id)."""
    records = await aload_log()
    groups: dict = {}

    for r in records:
        cid = r.get("campaign_id")
        if cid is None or cid in (0, "0", ""):
            cid = "unknown"
        group_key = str(cid)
        if group_key not in groups:
            groups[group_key] = {
                "campaign_key": r.get("campaign_key", "unknown"),
                "campaign_name": r.get("campaign_name", ""),
                "campaign_id": cid,
                "group_key": group_key,
                "count": 0,
                # Campaign removed in ListMonk; records are kept (audit trail).
                "campaign_deleted": False,
            }
        groups[group_key]["count"] += 1
        if r.get("campaign_deleted"):
            groups[group_key]["campaign_deleted"] = True
        if not groups[group_key]["campaign_name"] and r.get("campaign_name"):
            groups[group_key]["campaign_name"] = r.get("campaign_name")
        if (groups[group_key]["campaign_key"] == "unknown" or not groups[group_key]["campaign_key"]) and r.get("campaign_key") and r.get("campaign_key") != "unknown":
            groups[group_key]["campaign_key"] = r.get("campaign_key")

    # Sort: known campaign_keys descending (newest first), then 'unknown' at the bottom
    def _group_sort_key(g):
        ck = g.get("campaign_key") or ""
        is_known = 1 if ck and ck != "unknown" else 0
        return (is_known, ck, g.get("campaign_name", ""))

    sorted_groups = sorted(
        groups.values(),
        key=_group_sort_key,
        reverse=True,
    )
    return {"data": sorted_groups}


def _matches_campaign_id(record_cid: Any, target_cid: str) -> bool:
    """Match a record's campaign_id (int or None) against a string parameter.

    Handles numeric IDs ('12'), string IDs ('12'), and unattributed records
    grouped under 'unknown', 'none', 'null', '0', or empty string.
    """
    if target_cid.lower() in ("unknown", "none", "null", ""):
        return record_cid is None or record_cid in ("unknown", "", 0, "0")
    try:
        target_int = int(target_cid)
        return record_cid == target_int or str(record_cid) == target_cid
    except (ValueError, TypeError):
        return str(record_cid) == target_cid


@router.get("/campaign/{campaign_id}")
async def get_campaign_records(campaign_id: str,
                               page: int = Query(1, ge=1, le=MAX_PAGE),
                               per_page: int = Query(50, ge=1, le=MAX_PER_PAGE),
                               query: str = Query("", max_length=100),
                               source: str = Query("", max_length=20)):
    """Return paginated records for a specific campaign by its ID (or 'unknown')."""
    records = await aload_log()
    campaign_records = [r for r in records if _matches_campaign_id(r.get("campaign_id"), campaign_id)]

    total_unfiltered = len(campaign_records)
    link_count = sum(1 for r in campaign_records if r.get("source") == "link")
    email_count = total_unfiltered - link_count

    filtered_records = campaign_records
    if source:
        s = source.lower().strip()
        if s == "link":
            filtered_records = [r for r in filtered_records if r.get("source") == "link"]
        elif s == "email":
            filtered_records = [r for r in filtered_records if r.get("source") != "link"]
    if query:
        q = query.lower().strip()
        filtered_records = [
            r for r in filtered_records
            if q in (r.get("email") or "").lower()
            or q in (r.get("name") or "").lower()
            or q in (r.get("keyword") or "").lower()
        ]

    filtered_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    total = len(filtered_records)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": {
            "results": filtered_records[start:end],
            "total": total,
            "total_unfiltered": total_unfiltered,
            "link_count": link_count,
            "email_count": email_count,
            "page": page,
            "per_page": per_page,
            "campaign_id": campaign_id,
            "campaign_name": campaign_records[0].get("campaign_name", "") if campaign_records else "",
        }
    }


@router.delete("/campaign/{campaign_id}")
async def delete_campaign_group(campaign_id: str):
    """Delete all unsubscribe records for a specific campaign by its ID (or 'unknown')."""
    async with _log_lock:
        records = await aload_log()
        before_count = len(records)
        remaining = [r for r in records if not _matches_campaign_id(r.get("campaign_id"), campaign_id)]
        removed = before_count - len(remaining)
        await asave_log(remaining)
    return {"removed": removed, "message": f"Removed {removed} record(s) from campaign {campaign_id}"}


@router.delete("/records")
async def delete_records(emails: list[str] = Query(default=[]),
                         campaign_id: str | None = Query(default=None)):
    """Delete specific unsubscribe records by email, optionally scoped to a campaign."""
    async with _log_lock:
        records = await aload_log()
        before_count = len(records)
        if campaign_id is not None:
            remaining = [
                r for r in records
                if not (r.get("email") in emails and _matches_campaign_id(r.get("campaign_id"), campaign_id))
            ]
        else:
            remaining = [r for r in records if r.get("email") not in emails]
        removed = before_count - len(remaining)
        await asave_log(remaining)
    return {"removed": removed, "message": f"Removed {removed} record(s)"}


@router.get("/campaign/{campaign_id}/export")
async def export_campaign_csv(campaign_id: str):
    """Export a single campaign's unsubscribe records as CSV."""
    records = await aload_log()
    campaign_records = [r for r in records if _matches_campaign_id(r.get("campaign_id"), campaign_id)]
    if not campaign_records:
        raise HTTPException(status_code=404, detail="No records found for this campaign")

    campaign_records.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
    columns = ["email", "name", "source", "keyword", "campaign_name", "campaign_key", "timestamp"]
    safe_cid = "".join(c if c.isalnum() or c in "-_" else "_" for c in campaign_id)
    filename = f"unsubscribes_campaign_{safe_cid}.csv"
    return StreamingResponse(
        dict_list_to_csv(campaign_records, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export")
async def export_unsubscribes():
    """Export all unsubscribe records as CSV."""
    records = await aload_log()
    if not records:
        raise HTTPException(status_code=404, detail="No unsubscribe records found")

    records.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
    columns = ["email", "name", "source", "campaign_name", "campaign_key", "campaign_id", "keyword", "subject", "subscriber_id", "timestamp"]
    return StreamingResponse(
        dict_list_to_csv(records, columns),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=unsubscribes_export.csv"},
    )


async def _perform_scan() -> dict:
    try:
        imap_result = await scan_and_unsubscribe(listmonk)
        link_result = await scan_link_unsubscribes(listmonk)
    except Exception as e:
        logging.getLogger("unsubscribes").error(f"unsubscribe scan failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Unsubscribe scan failed; see the server log for details.",
        )
    total_processed = imap_result.get("processed", 0) + link_result.get("processed", 0)
    return {
        "imap": imap_result,
        "link": link_result,
        "message": f"Scan complete: {total_processed} unsubscribed",
    }


async def _run_scan_job() -> None:
    global _scan_job
    try:
        result = await _perform_scan()
        _scan_job = {"status": "finished", "result": result}
    except HTTPException as e:
        _scan_job = {"status": "failed", "error": e.detail}
    except Exception as e:
        logging.getLogger("unsubscribes").error(f"background scan failed: {e}", exc_info=True)
        _scan_job = {"status": "failed", "error": "Unsubscribe scan failed"}


@router.post("/scan")
async def trigger_scan(background: bool = False):
    """Manually trigger both IMAP and link-based unsubscribe scans.

    ``background=true`` starts the scan and returns 202; poll /scan/status.
    """
    global _scan_job
    if background:
        if _scan_job and _scan_job.get("status") == "running":
            return JSONResponse(status_code=202, content={"status": "running"})
        _scan_job = {"status": "running"}
        spawn(_run_scan_job())
        return JSONResponse(status_code=202, content={"status": "started"})
    return await _perform_scan()


@router.get("/scan/status")
async def scan_status():
    return _scan_job or {"status": "idle"}


@router.delete("/clear")
async def clear_unsubscribes():
    """Clear all unsubscribe records to free storage."""
    async with _log_lock:
        records = await aload_log()
        count = len(records)
        await asave_log([])
    return {"cleared": count, "message": f"Removed {count} record(s)"}


async def _perform_reset(records: list[dict]) -> dict:
    """Undo every current unsubscribe record (shared by sync/background mode)."""
    sem = asyncio.Semaphore(RESET_CONCURRENCY)
    results: list[dict] = [None] * len(records)  # type: ignore[list-item]

    # Two log records can share a subscriber (e.g. reply-unsubscribe, then
    # link-unsubscribe after re-subscribing). Restore that subscriber once,
    # but retain per-record outcome counts and duplicate-key handling.
    groups: dict[tuple, dict] = {}
    for idx, r in enumerate(records):
        key = (r.get("subscriber_id"), r.get("email", "unknown"))
        group = groups.setdefault(key, {"indices": [], "lists_removed": set()})
        group["indices"].append(idx)
        for lid in r.get("lists_removed") or []:
            group["lists_removed"].add(lid)

    async def restore(key: tuple, group: dict) -> None:
        sub_id, email_addr = key
        indices = group["indices"]
        lists_removed = sorted(group["lists_removed"])

        def finish(ok: bool, detail: str) -> None:
            for idx in indices:
                results[idx] = {"ok": ok, "key": key, "detail": detail}

        if not sub_id:
            finish(False, f"SKIP {email_addr}: no subscriber_id")
            return

        async with sem:
            try:
                # Step 1: Fetch current subscriber data from ListMonk
                result = await listmonk.get_subscriber(sub_id)
                subscriber = result.get("data") or {}

                if not subscriber:
                    finish(False, f"SKIP {email_addr}: subscriber {sub_id} not found")
                    return

                # Step 2: Re-enable subscriber (remove blocklist)
                current_lists = [lst["id"] for lst in (subscriber.get("lists") or [])]
                await listmonk.update_subscriber(sub_id, {
                    "email": subscriber["email"],
                    "name": subscriber.get("name") or "",
                    "status": "enabled",
                    "lists": current_lists,
                    "attribs": subscriber.get("attribs") or {},
                })

                # Step 3: Re-subscribe to removed lists
                if lists_removed:
                    await listmonk.modify_list_memberships({
                        "ids": [sub_id],
                        "action": "add",
                        "target_list_ids": lists_removed,
                        "status": "confirmed",
                    })

                finish(
                    True,
                    f"OK {email_addr}: enabled + re-added to lists {lists_removed}",
                )

            except Exception as e:
                logging.getLogger("unsubscribes").error(
                    f"reset failed for {email_addr}: {e}", exc_info=True,
                )
                finish(False, f"FAIL {email_addr}: processing failed")

    await asyncio.gather(*(restore(k, g) for k, g in groups.items()))

    details: list[str] = []
    restored_keys: set = set()
    restored = 0
    for res in results:
        if res is None:
            continue
        details.append(res["detail"])
        if res["ok"]:
            restored += 1
            restored_keys.add(res["key"])
            logging.getLogger("unsubscribes").info(res["detail"])
        else:
            logging.getLogger("unsubscribes").error(res["detail"])

    failed = len(records) - restored

    # Only clear records that were successfully restored;
    # failed records and newly arrived records stay in the log.
    async with _log_lock:
        fresh_records = await aload_log()
        remaining_records = [
            r for r in fresh_records
            if (r.get("subscriber_id"), r.get("email", "unknown")) not in restored_keys
        ]
        await asave_log(remaining_records)

    # Allow future unsubscribes from restored subscribers to be detected again.
    # (The suppressed-email set is intentionally NOT cleared when log records
    # are merely deleted — otherwise the scanners would re-handle them.)
    restored_emails = [email for _, email in restored_keys]
    if restored_emails:
        await unmark_processed(restored_emails)

    return {
        "message": f"Reset complete: {restored} restored, {failed} failed (kept in log for retry)",
        "restored": restored,
        "failed": failed,
        "details": details,
    }


async def _run_reset_job(records: list[dict]) -> None:
    global _reset_job
    try:
        result = await _perform_reset(records)
        _reset_job = {"status": "finished", "result": result}
    except HTTPException as e:
        _reset_job = {"status": "failed", "error": e.detail}
    except Exception as e:
        logging.getLogger("unsubscribes").error(f"background reset failed: {e}", exc_info=True)
        _reset_job = {"status": "failed", "error": "Reset failed"}


@router.post("/reset")
async def reset_all_unsubscribes(background: bool = False):
    """
    UNDO all unsubscribe actions: re-subscribe users to their original lists,
    remove blocklist status, and remove restored records from the log.
    Failed records remain in the log so they can be retried.

    ``background=true`` starts the work and returns 202; poll /reset/status.
    """
    global _reset_job
    records = await aload_log()
    if not records:
        return {"message": "No records to reset", "restored": 0, "failed": 0}

    if len(records) > RESET_MAX_RECORDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Refusing to reset {len(records)} records at once "
                f"(limit {RESET_MAX_RECORDS}). Remove records per campaign first."
            ),
        )

    if background:
        if _reset_job and _reset_job.get("status") == "running":
            return JSONResponse(status_code=202, content={"status": "running"})
        _reset_job = {"status": "running"}
        spawn(_run_reset_job(records))
        return JSONResponse(status_code=202, content={"status": "started"})

    return await _perform_reset(records)


@router.get("/reset/status")
async def reset_status():
    return _reset_job or {"status": "idle"}
