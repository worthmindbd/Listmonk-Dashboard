from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.listmonk_client import listmonk
from app.services.export_service import dict_list_to_csv

router = APIRouter()


@router.get("")
async def get_subscribers(page: int = 1, per_page: int = 50,
                          query: str = "", list_id: Optional[int] = None):
    return await listmonk.get_subscribers(page, per_page, query, list_id)


@router.get("/export-all")
async def export_all_subscribers(query: str = "", list_id: Optional[int] = None):
    """Export all subscribers as CSV by paginating through the API."""
    all_subscribers = await listmonk.paginate_all(
        listmonk.get_subscribers, per_page=100, query=query, list_id=list_id,
    )
    if not all_subscribers:
        raise HTTPException(status_code=404, detail="No subscribers found")

    columns = ["id", "email", "name", "status", "created_at", "updated_at"]
    return StreamingResponse(
        dict_list_to_csv(all_subscribers, columns),
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


@router.post("/import")
async def import_subscribers(file: bytes, params: dict):
    return await listmonk.import_subscribers(file, "import.csv", params)


@router.put("/lists")
async def modify_list_memberships(data: dict):
    return await listmonk.modify_list_memberships(data)


@router.put("/{subscriber_id}")
async def update_subscriber(subscriber_id: int, data: dict):
    return await listmonk.update_subscriber(subscriber_id, data)


@router.put("/{subscriber_id}/blocklist")
async def blocklist_subscriber(subscriber_id: int):
    return await listmonk.blocklist_subscriber(subscriber_id)


@router.put("/blocklist")
async def blocklist_subscribers(data: dict):
    return await listmonk.blocklist_subscribers(data.get("ids", []))


@router.delete("/{subscriber_id}")
async def delete_subscriber(subscriber_id: int):
    return await listmonk.delete_subscriber(subscriber_id)


@router.delete("")
async def delete_subscribers(ids: list[int] = Query(...)):
    return await listmonk.delete_subscribers(ids)
