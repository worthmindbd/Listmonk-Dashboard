from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.listmonk_client import listmonk
from app.services.export_service import aiter_dicts_to_csv
from app.services.imap_helpers import subscriber_search_query

router = APIRouter()

# Pagination bounds (the UI requests at most 50).
MAX_PAGE = 100
MAX_PER_PAGE = 200

# Export streams pages lazily, so memory stays at one page however large the
# list is. The page cap only guards against an API that never stops returning
# rows; it must sit far above any real list — the old 100 x 100 cap silently
# truncated every export at 10k subscribers. 2000 pages x 500 = 1M rows.
EXPORT_PAGE_SIZE = 500
EXPORT_MAX_PAGES = 2000


@router.get("")
async def get_subscribers(page: int = Query(1, ge=1, le=MAX_PAGE),
                          per_page: int = Query(50, ge=1, le=MAX_PER_PAGE),
                          query: str = "", search: str = "",
                          list_id: Optional[int] = None):
    """List subscribers.

    ``search`` is the UI's plain-text search (name/email). ListMonk's ``query``
    is a raw SQL expression and is kept for internal/advanced callers.
    """
    if len(search) > 200:
        raise HTTPException(status_code=400, detail="search is too long")
    effective_query = subscriber_search_query(search) if search else query
    return await listmonk.get_subscribers(page, per_page, effective_query, list_id)


@router.get("/export-all")
async def export_all_subscribers(query: str = "", search: str = "",
                                 list_id: Optional[int] = None):
    """Stream every subscriber as CSV, page by page.

    Rows are pulled from ListMonk lazily so peak memory stays at one page
    regardless of list size. 404 when the filter matches nothing.
    """
    if len(search) > 200:
        raise HTTPException(status_code=400, detail="search is too long")
    effective_query = subscriber_search_query(search) if search else query
    columns = ["id", "email", "name", "status", "created_at", "updated_at"]

    async def rows():
        async for sub in listmonk.iter_pages(
            listmonk.get_subscribers, per_page=EXPORT_PAGE_SIZE,
            max_pages=EXPORT_MAX_PAGES,
            query=effective_query, list_id=list_id,
        ):
            yield sub

    # Peek one row so an empty result is still a 404 rather than a 0-byte file.
    stream = rows()
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        raise HTTPException(status_code=404, detail="No subscribers found")

    async def with_first():
        yield first
        async for sub in stream:
            yield sub

    return StreamingResponse(
        aiter_dicts_to_csv(with_first(), columns),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers_export.csv"},
    )


@router.get("/import/status")
async def get_import_status():
    return await listmonk.get_import_status()


@router.get("/import/logs")
async def get_import_logs():
    return await listmonk.get_import_logs()


@router.get("/{subscriber_id}")
async def get_subscriber(subscriber_id: int):
    return await listmonk.get_subscriber(subscriber_id)


@router.get("/{subscriber_id}/export")
async def export_subscriber(subscriber_id: int):
    return await listmonk.export_subscriber(subscriber_id)


@router.get("/{subscriber_id}/bounces")
async def get_subscriber_bounces(subscriber_id: int):
    return await listmonk.get_subscriber_bounces(subscriber_id)


@router.post("")
async def create_subscriber(data: dict):
    return await listmonk.create_subscriber(data)


# NOTE: static routes like /lists and /blocklist MUST be registered before
# /{subscriber_id}, otherwise Starlette matches the parameterized route first
# and int-parses "lists"/"blocklist" (422), making the endpoints unreachable.
@router.put("/lists")
async def modify_list_memberships(data: dict):
    return await listmonk.modify_list_memberships(data)


@router.put("/blocklist")
async def blocklist_subscribers(data: dict):
    return await listmonk.blocklist_subscribers(data.get("ids") or [])


@router.put("/{subscriber_id}")
async def update_subscriber(subscriber_id: int, data: dict):
    """Update a subscriber.

    ListMonk's PUT is a full update: omitting ``lists`` clears every list
    membership, and omitting ``email``/``name``/``status``/``attribs`` wipes
    or rejects the record. Fetch the current subscriber and merge omitted
    fields when a caller sends a partial update.
    """
    payload = dict(data)
    needs_lists = "lists" not in payload or payload.get("lists") is None
    needs_fields = any(
        k not in payload or payload.get(k) is None
        for k in ("email", "name", "status", "attribs")
    )
    if needs_lists or (needs_fields and hasattr(listmonk, "get_subscriber")):
        current = await listmonk.get_subscriber(subscriber_id)
        subscriber = (current or {}).get("data") or {}
        if needs_lists:
            payload["lists"] = [
                lst.get("id") for lst in (subscriber.get("lists") or [])
                if isinstance(lst, dict) and lst.get("id")
            ]
        for key in ("email", "name", "status", "attribs"):
            if (key not in payload or payload.get(key) is None) and key in subscriber:
                payload[key] = subscriber[key]
    return await listmonk.update_subscriber(subscriber_id, payload)


@router.put("/{subscriber_id}/blocklist")
async def blocklist_subscriber(subscriber_id: int):
    return await listmonk.blocklist_subscriber(subscriber_id)


@router.delete("/{subscriber_id}")
async def delete_subscriber(subscriber_id: int):
    return await listmonk.delete_subscriber(subscriber_id)


@router.delete("")
async def delete_subscribers(ids: list[int] = Query(...)):
    return await listmonk.delete_subscribers(ids)
