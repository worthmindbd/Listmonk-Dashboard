import asyncio
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.listmonk_client import listmonk
from app.services.bounce_ingest import ingest_bounce_mailbox
from app.services.bounce_list import fetch_all_filtered_bounces, fetch_filtered_bounces_page
from app.services.export_service import dict_list_to_csv
from app.services.hard_bounce_cache import update_hard_bounce_counts

router = APIRouter()
logger = logging.getLogger(__name__)

_DELETE_CONCURRENCY = 10


@router.post("/ingest")
async def ingest_bounces():
    """Scan the bounce IMAP mailbox for new bounces, classify each, and
    create matching bounce records in ListMonk."""
    try:
        result = await ingest_bounce_mailbox(listmonk)
        asyncio.create_task(update_hard_bounce_counts())
        return result
    except Exception as e:
        logger.error(f"bounce ingest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def get_bounces(page: int = 1, per_page: int = 50,
                      campaign_id: Optional[int] = None, source: str = "",
                      bounce_type: str = ""):
    if not bounce_type:
        return await listmonk.get_bounces(page, per_page, campaign_id, source)

    return await fetch_filtered_bounces_page(
        listmonk, page, per_page, bounce_type, campaign_id, source,
    )


@router.get("/export")
async def export_bounces(campaign_id: Optional[int] = None, source: str = "",
                         bounce_type: str = ""):
    """Export all bounce records (optionally filtered) as CSV."""
    all_bounces = await fetch_all_filtered_bounces(
        listmonk, bounce_type, campaign_id, source,
    )

    if not all_bounces:
        raise HTTPException(status_code=404, detail="No bounce records found")

    for b in all_bounces:
        b["campaign_name"] = b.get("campaign", {}).get("name", "")
        b["campaign_id"] = b.get("campaign", {}).get("id", "")

    columns = ["id", "email", "campaign_id", "campaign_name", "type", "source", "created_at"]
    suffix = ""
    if campaign_id:
        suffix += f"_campaign_{campaign_id}"
    if bounce_type:
        suffix += f"_{bounce_type}"
    return StreamingResponse(
        dict_list_to_csv(all_bounces, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=bounces{suffix}.csv"},
    )


@router.delete("/{bounce_id}")
async def delete_bounce(bounce_id: int):
    return await listmonk.delete_bounce(bounce_id)


@router.delete("")
async def delete_all_bounces(campaign_id: Optional[int] = None,
                              bounce_type: str = ""):
    """Delete bounces. If campaign_id is provided, only delete bounces for
    that campaign (iterating + deleting in parallel). If bounce_type is set
    without campaign_id, delete all matching bounces. Otherwise delete all."""
    if not campaign_id and not bounce_type:
        return await listmonk.delete_all_bounces()

    all_bounces = await fetch_all_filtered_bounces(
        listmonk, bounce_type, campaign_id,
    )
    bounce_ids = [b["id"] for b in all_bounces]

    if not bounce_ids:
        return {"deleted": 0, "errors": 0, "campaign_id": campaign_id}

    sem = asyncio.Semaphore(_DELETE_CONCURRENCY)
    deleted = 0
    errors = 0

    async def _delete(bid: int):
        nonlocal deleted, errors
        async with sem:
            try:
                await listmonk.delete_bounce(bid)
                deleted += 1
            except Exception as exc:
                errors += 1
                logger.error(f"Failed to delete bounce {bid}: {exc}")

    await asyncio.gather(*(_delete(bid) for bid in bounce_ids))
    return {"deleted": deleted, "errors": errors, "campaign_id": campaign_id}
