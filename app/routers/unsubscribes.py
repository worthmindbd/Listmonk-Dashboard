from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.services.imap_unsubscribe import (
    load_log, save_log, get_stats, check_imap_status, scan_and_unsubscribe,
)
from app.services.listmonk_client import listmonk
from app.services.export_service import dict_list_to_csv

router = APIRouter()


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
    """Return unsubscribe records grouped by campaign (month/year key)."""
    records = load_log()
    groups: dict[str, dict] = {}

    for r in records:
        key = r.get("campaign_key", "unknown")
        if key not in groups:
            groups[key] = {
                "campaign_key": key,
                "campaign_name": r.get("campaign_name", ""),
                "campaign_id": r.get("campaign_id"),
                "count": 0,
                "records": [],
            }
        groups[key]["count"] += 1

    # Sort by key descending (most recent month first)
    sorted_groups = sorted(groups.values(), key=lambda g: g["campaign_key"], reverse=True)
    return {"data": sorted_groups}


@router.get("/campaign/{key}")
async def get_campaign_records(key: str, page: int = 1, per_page: int = 50):
    """Return paginated records for a specific campaign group."""
    records = load_log()
    campaign_records = [r for r in records if r.get("campaign_key") == key]
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
            "campaign_key": key,
            "campaign_name": campaign_records[0].get("campaign_name", "") if campaign_records else "",
        }
    }


@router.delete("/campaign/{key}")
async def delete_campaign_group(key: str):
    """Delete all unsubscribe records for a specific campaign group."""
    records = load_log()
    before_count = len(records)
    remaining = [r for r in records if r.get("campaign_key") != key]
    removed = before_count - len(remaining)
    save_log(remaining)
    return {"removed": removed, "message": f"Removed {removed} record(s) from campaign {key}"}


@router.post("/records/delete")
async def delete_records(body: dict):
    """Delete specific records by email address."""
    emails_to_delete = set(body.get("emails", []))
    if not emails_to_delete:
        raise HTTPException(status_code=400, detail="No emails provided")

    records = load_log()
    before_count = len(records)
    remaining = [r for r in records if r.get("email") not in emails_to_delete]
    removed = before_count - len(remaining)
    save_log(remaining)
    return {"removed": removed, "message": f"Removed {removed} record(s)"}


@router.get("/campaign/{key}/export")
async def export_campaign_csv(key: str):
    """Export a single campaign's unsubscribe records as CSV."""
    records = load_log()
    campaign_records = [r for r in records if r.get("campaign_key") == key]
    if not campaign_records:
        raise HTTPException(status_code=404, detail="No records found for this campaign")

    campaign_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    columns = ["email", "name", "keyword", "campaign_name", "campaign_key", "timestamp"]
    filename = f"unsubscribes_{key.replace('/', '-')}.csv"
    return StreamingResponse(
        dict_list_to_csv(campaign_records, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/by-campaign-id/{campaign_id}/export")
async def export_by_campaign_id(campaign_id: int):
    """Export unsubscribe records filtered by ListMonk campaign ID as CSV."""
    records = load_log()
    filtered = [r for r in records if r.get("campaign_id") == campaign_id]
    if not filtered:
        raise HTTPException(status_code=404, detail="No unsubscribe records for this campaign")

    filtered.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    columns = ["email", "name", "keyword", "campaign_name", "campaign_key", "timestamp"]
    filename = f"unsubscribes_campaign_{campaign_id}.csv"
    return StreamingResponse(
        dict_list_to_csv(filtered, columns),
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
