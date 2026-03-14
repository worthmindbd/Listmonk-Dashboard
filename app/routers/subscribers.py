from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.listmonk_client import listmonk
from app.services.export_service import dict_list_to_csv
import httpx

router = APIRouter()


@router.get("")
async def get_subscribers(page: int = 1, per_page: int = 50,
                          query: str = "", list_id: Optional[int] = None):
    try:
        return await listmonk.get_subscribers(page, per_page, query, list_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/export-all")
async def export_all_subscribers(query: str = "", list_id: Optional[int] = None):
    """Export all subscribers as CSV by paginating through the API."""
    try:
        all_subscribers = []
        page = 1
        per_page = 100
        while True:
            result = await listmonk.get_subscribers(page, per_page, query, list_id)
            data = result.get("data", {})
            results = data.get("results", [])
            if not results:
                break
            all_subscribers.extend(results)
            total = data.get("total", 0)
            if page * per_page >= total:
                break
            page += 1

        if not all_subscribers:
            raise HTTPException(status_code=404, detail="No subscribers found")

        columns = ["id", "email", "name", "status", "created_at", "updated_at"]
        return StreamingResponse(
            dict_list_to_csv(all_subscribers, columns),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=subscribers_export.csv"}
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/import/status")
async def get_import_status():
    try:
        return await listmonk.get_import_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/import/logs")
async def get_import_logs():
    try:
        return await listmonk.get_import_logs()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{subscriber_id}")
async def get_subscriber(subscriber_id: int):
    try:
        return await listmonk.get_subscriber(subscriber_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{subscriber_id}/export")
async def export_subscriber(subscriber_id: int):
    try:
        return await listmonk.export_subscriber(subscriber_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{subscriber_id}/bounces")
async def get_subscriber_bounces(subscriber_id: int):
    try:
        return await listmonk.get_subscriber_bounces(subscriber_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.post("")
async def create_subscriber(data: dict):
    try:
        return await listmonk.create_subscriber(data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.post("/import")
async def import_subscribers(file: bytes, params: dict):
    try:
        return await listmonk.import_subscribers(file, "import.csv", params)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/lists")
async def modify_list_memberships(data: dict):
    try:
        return await listmonk.modify_list_memberships(data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{subscriber_id}")
async def update_subscriber(subscriber_id: int, data: dict):
    try:
        return await listmonk.update_subscriber(subscriber_id, data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{subscriber_id}/blocklist")
async def blocklist_subscriber(subscriber_id: int):
    try:
        return await listmonk.blocklist_subscriber(subscriber_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/blocklist")
async def blocklist_subscribers(data: dict):
    try:
        return await listmonk.blocklist_subscribers(data.get("ids", []))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.delete("/{subscriber_id}")
async def delete_subscriber(subscriber_id: int):
    try:
        return await listmonk.delete_subscriber(subscriber_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.delete("")
async def delete_subscribers(ids: list[int] = Query(...)):
    try:
        return await listmonk.delete_subscribers(ids)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
