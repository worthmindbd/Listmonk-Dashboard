from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from app.services.imap_unsubscribe import (
    load_log, save_log, get_stats, check_imap_status, scan_and_unsubscribe,
    load_settings, save_settings,
)
from app.services.listmonk_client import listmonk
from app.services.export_service import dict_list_to_csv

router = APIRouter()


@router.get("/settings")
async def get_unsub_settings():
    """Return current unsubscribe scanner settings."""
    return load_settings()


@router.put("/settings")
async def update_unsub_settings(request: Request):
    """Update unsubscribe scanner settings (e.g., blocklist toggle)."""
    data = await request.json()
    save_settings(data)
    return load_settings()


@router.get("")
async def get_unsubscribes(page: int = 1, per_page: int = 25):
    """Return paginated unsubscribe records (newest first)."""
    records = load_log()
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
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
async def get_unsubscribe_stats(campaign_id: int = 0):
    """Return aggregate unsubscribe counts. Optionally filter by campaign_id."""
    stats = get_stats()
    if campaign_id:
        records = load_log()
        campaign_count = sum(1 for r in records if r.get("campaign_id") == campaign_id)
        stats["campaign_count"] = campaign_count
    return stats


@router.get("/imap-status")
async def get_imap_status():
    """Check if IMAP is configured and can connect."""
    return check_imap_status()


@router.get("/campaigns")
async def get_campaign_groups():
    """Return unsubscribe records grouped by individual campaign (campaign_id)."""
    records = load_log()
    groups: dict = {}

    for r in records:
        cid = r.get("campaign_id") or "unknown"
        group_key = str(cid)
        if group_key not in groups:
            groups[group_key] = {
                "campaign_key": r.get("campaign_key", "unknown"),
                "campaign_name": r.get("campaign_name", ""),
                "campaign_id": cid,
                "group_key": group_key,
                "count": 0,
            }
        groups[group_key]["count"] += 1

    # Sort by campaign_key descending (most recent month first), then by campaign name
    sorted_groups = sorted(
        groups.values(),
        key=lambda g: (g["campaign_key"], g.get("campaign_name", "")),
        reverse=True,
    )
    return {"data": sorted_groups}


@router.get("/campaign/{campaign_id}")
async def get_campaign_records(campaign_id: int, page: int = 1, per_page: int = 50):
    """Return paginated records for a specific campaign by its ID."""
    records = load_log()
    campaign_records = [r for r in records if r.get("campaign_id") == campaign_id]
    campaign_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    total = len(campaign_records)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": {
            "results": campaign_records[start:end],
            "total": total,
            "page": page,
            "per_page": per_page,
            "campaign_id": campaign_id,
            "campaign_name": campaign_records[0].get("campaign_name", "") if campaign_records else "",
        }
    }


@router.delete("/campaign/{campaign_id}")
async def delete_campaign_group(campaign_id: int):
    """Delete all unsubscribe records for a specific campaign by its ID."""
    records = load_log()
    before_count = len(records)
    remaining = [r for r in records if r.get("campaign_id") != campaign_id]
    removed = before_count - len(remaining)
    save_log(remaining)
    return {"removed": removed, "message": f"Removed {removed} record(s) from campaign {campaign_id}"}


@router.delete("/records")
async def delete_records(emails: list[str] = Query(default=[])):
    """Delete specific unsubscribe records by email."""
    records = load_log()
    before_count = len(records)
    remaining = [r for r in records if r.get("email") not in emails]
    removed = before_count - len(remaining)
    save_log(remaining)
    return {"removed": removed, "message": f"Removed {removed} record(s)"}


@router.get("/campaign/{campaign_id}/export")
async def export_campaign_csv(campaign_id: int):
    """Export a single campaign's unsubscribe records as CSV."""
    records = load_log()
    campaign_records = [r for r in records if r.get("campaign_id") == campaign_id]
    if not campaign_records:
        raise HTTPException(status_code=404, detail="No records found for this campaign")

    campaign_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    columns = ["email", "name", "keyword", "campaign_name", "campaign_key", "timestamp"]
    filename = f"unsubscribes_campaign_{campaign_id}.csv"
    return StreamingResponse(
        dict_list_to_csv(campaign_records, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export")
async def export_unsubscribes():
    """Export all unsubscribe records as CSV."""
    records = load_log()
    if not records:
        raise HTTPException(status_code=404, detail="No unsubscribe records found")

    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    columns = ["email", "name", "campaign_name", "campaign_key", "campaign_id", "keyword", "subject", "subscriber_id", "timestamp"]
    return StreamingResponse(
        dict_list_to_csv(records, columns),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=unsubscribes_export.csv"},
    )


@router.post("/scan")
async def trigger_scan():
    """Manually trigger an IMAP scan."""
    try:
        return await scan_and_unsubscribe(listmonk)
    except Exception as e:
        return {"error": str(e)}


@router.delete("/clear")
async def clear_unsubscribes():
    """Clear all unsubscribe records to free storage."""
    records = load_log()
    count = len(records)
    save_log([])
    return {"cleared": count, "message": f"Removed {count} record(s)"}


@router.post("/reset")
async def reset_all_unsubscribes():
    """
    UNDO all unsubscribe actions: re-subscribe users to their original lists,
    remove blocklist status, and clear the unsubscribe log.
    Use this to start fresh before re-scanning.
    """
    records = load_log()
    if not records:
        return {"message": "No records to reset", "restored": 0, "failed": 0}

    restored = 0
    failed = 0
    details = []

    for r in records:
        sub_id = r.get("subscriber_id")
        email_addr = r.get("email", "unknown")
        lists_removed = r.get("lists_removed", [])

        if not sub_id:
            failed += 1
            details.append(f"SKIP {email_addr}: no subscriber_id")
            continue

        try:
            # Step 1: Fetch current subscriber data from ListMonk
            result = await listmonk.get_subscriber(sub_id)
            subscriber = result.get("data", {})

            if not subscriber:
                failed += 1
                details.append(f"SKIP {email_addr}: subscriber {sub_id} not found")
                continue

            # Step 2: Re-enable subscriber (remove blocklist)
            current_lists = [lst["id"] for lst in subscriber.get("lists", [])]
            await listmonk.update_subscriber(sub_id, {
                "email": subscriber["email"],
                "name": subscriber.get("name", ""),
                "status": "enabled",
                "lists": current_lists,
                "attribs": subscriber.get("attribs", {}),
            })

            # Step 3: Re-subscribe to removed lists
            if lists_removed:
                await listmonk.modify_list_memberships({
                    "ids": [sub_id],
                    "action": "add",
                    "target_list_ids": lists_removed,
                    "status": "confirmed",
                })

            restored += 1
            details.append(f"OK {email_addr}: enabled + re-added to lists {lists_removed}")
            print(f"[RESET] Restored: {email_addr} (lists: {lists_removed})")

        except Exception as e:
            failed += 1
            details.append(f"FAIL {email_addr}: {e}")
            print(f"[RESET] Failed: {email_addr}: {e}")

    # Clear the log after processing
    save_log([])

    return {
        "message": f"Reset complete: {restored} restored, {failed} failed",
        "restored": restored,
        "failed": failed,
        "details": details,
    }
