import asyncio
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.listmonk_client import listmonk
from app.services.bounce_ingest import ingest_bounce_mailbox
from app.services.export_service import dict_list_to_csv
from app.services.hard_bounce_cache import update_hard_bounce_counts

router = APIRouter()
logger = logging.getLogger(__name__)

# In-memory cache for filtered bounces
_FILTER_CACHE: dict[str, list[dict]] = {}


def _cache_key(campaign_id: Optional[int], source: str, bounce_type: str) -> str:
    """Generate cache key for bounce filter."""
    return f"{campaign_id or 'all'}:{source}:{bounce_type}"


def _invalidate_cache():
    """Clear the bounce filter cache."""
    global _FILTER_CACHE
    _FILTER_CACHE.clear()

_DELETE_CONCURRENCY = 10


@router.post("/ingest")
async def ingest_bounces():
    """Scan the bounce IMAP mailbox for new bounces, classify each, and
    create matching bounce records in ListMonk."""
    try:
        result = await ingest_bounce_mailbox(listmonk)
        asyncio.create_task(update_hard_bounce_counts())  # Update cache in background
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

    # Client-side filter with caching
    key = _cache_key(campaign_id, source, bounce_type)
    if key not in _FILTER_CACHE:
        all_bounces = await listmonk.paginate_all(
            listmonk.get_bounces, per_page=500,
            campaign_id=campaign_id, source=source,
        )
        _FILTER_CACHE[key] = [b for b in all_bounces if b.get("type") == bounce_type]

    filtered = _FILTER_CACHE[key]
    total = len(filtered)
    start = (page - 1) * per_page
    return {"data": {"results": filtered[start:start + per_page], "total": total}}


@router.get("/export")
async def export_bounces(campaign_id: Optional[int] = None, source: str = "",
                         bounce_type: str = ""):
    """Export all bounce records (optionally filtered) as CSV."""
    all_bounces = await listmonk.paginate_all(
        listmonk.get_bounces, per_page=500,
        campaign_id=campaign_id, source=source,
    )

    if bounce_type:
        all_bounces = [b for b in all_bounces if b.get("type") == bounce_type]

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
    _invalidate_cache()
    return await listmonk.delete_bounce(bounce_id)


@router.delete("")
async def delete_all_bounces(campaign_id: Optional[int] = None,
                              bounce_type: str = ""):
    """Delete bounces. If campaign_id is provided, only delete bounces for
    that campaign (iterating + deleting in parallel). Otherwise delete all."""
    if not campaign_id:
        _invalidate_cache()
        return await listmonk.delete_all_bounces()

    all_bounces = await listmonk.paginate_all(
        listmonk.get_bounces, per_page=500, campaign_id=campaign_id,
    )

    if bounce_type:
        all_bounces = [b for b in all_bounces if b.get("type") == bounce_type]

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
    _invalidate_cache()
    return {"deleted": deleted, "errors": errors, "campaign_id": campaign_id}
