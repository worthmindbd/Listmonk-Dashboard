import csv
import io
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from typing import Optional
from app.services.listmonk_client import listmonk
import httpx

router = APIRouter()


@router.get("")
async def get_bounces(page: int = 1, per_page: int = 50,
                      campaign_id: Optional[int] = None, source: str = ""):
    try:
        return await listmonk.get_bounces(page, per_page, campaign_id, source)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/export")
async def export_bounces(campaign_id: Optional[int] = None, source: str = ""):
    """Export all bounce records (optionally filtered) as CSV."""
    try:
        all_bounces = []
        page = 1
        while True:
            result = await listmonk.get_bounces(page, 500, campaign_id, source)
            data = result.get("data", {})
            results = data.get("results", [])
            if not results:
                break
            for b in results:
                b["campaign_name"] = b.get("campaign", {}).get("name", "")
                b["campaign_id"] = b.get("campaign", {}).get("id", "")
            all_bounces.extend(results)
            if page * 500 >= data.get("total", 0):
                break
            page += 1

        if not all_bounces:
            raise HTTPException(status_code=404, detail="No bounce records found")

        output = io.StringIO()
        columns = ["id", "email", "campaign_id", "campaign_name", "type", "source", "created_at"]
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_bounces)

        suffix = f"_campaign_{campaign_id}" if campaign_id else ""
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=bounces{suffix}.csv"},
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.delete("/{bounce_id}")
async def delete_bounce(bounce_id: int):
    try:
        return await listmonk.delete_bounce(bounce_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.delete("")
async def delete_all_bounces():
    try:
        return await listmonk.delete_all_bounces()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
