import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from typing import Optional
from app.services.listmonk_client import listmonk
from app.services.export_service import dict_list_to_csv
import httpx

router = APIRouter()


@router.get("")
async def get_campaigns(page: int = 1, per_page: int = 50,
                        query: str = "", status: str = "",
                        order_by: str = "created_at", order: str = "DESC"):
    try:
        return await listmonk.get_campaigns(page, per_page, query, status,
                                            order_by, order)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/running/stats")
async def get_running_stats(campaign_id: Optional[int] = None):
    try:
        return await listmonk.get_running_stats(campaign_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/analytics/{analytics_type}")
async def get_campaign_analytics(analytics_type: str,
                                 campaign_id: int = 0,
                                 from_date: str = "", to_date: str = ""):
    try:
        return await listmonk.get_campaign_analytics(analytics_type, campaign_id,
                                                     from_date, to_date)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/analytics/{analytics_type}/export")
async def export_campaign_analytics(analytics_type: str,
                                    campaign_id: int = 0,
                                    from_date: str = "", to_date: str = ""):
    """Export campaign analytics as CSV."""
    try:
        result = await listmonk.get_campaign_analytics(analytics_type, campaign_id,
                                                       from_date, to_date)
        data = result.get("data", [])
        if not data:
            raise HTTPException(status_code=404, detail="No analytics data found")

        columns = list(data[0].keys()) if data else []
        return StreamingResponse(
            dict_list_to_csv(data, columns),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=analytics_{analytics_type}.csv"}
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/export-all")
async def export_all_campaigns():
    """Export all campaigns summary as CSV."""
    try:
        all_campaigns = []
        page = 1
        while True:
            result = await listmonk.get_campaigns(page, 100)
            data = result.get("data", {})
            results = data.get("results", [])
            if not results:
                break
            all_campaigns.extend(results)
            if page * 100 >= data.get("total", 0):
                break
            page += 1

        if not all_campaigns:
            raise HTTPException(status_code=404, detail="No campaigns found")

        columns = ["id", "name", "subject", "status", "type", "to_send", "sent",
                    "views", "clicks", "bounces", "created_at", "started_at"]
        return StreamingResponse(
            dict_list_to_csv(all_campaigns, columns),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=campaigns_export.csv"}
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{campaign_id}/subscribers/{engagement_type}")
async def get_campaign_subscribers(campaign_id: int, engagement_type: str,
                                   page: int = 1, per_page: int = 50):
    """Get subscribers who viewed/clicked/bounced for a campaign."""
    sql_map = {
        "views": f"subscribers.id IN (SELECT subscriber_id FROM campaign_views WHERE campaign_id={campaign_id})",
        "clicks": f"subscribers.id IN (SELECT subscriber_id FROM link_clicks WHERE campaign_id={campaign_id})",
    }

    if engagement_type == "bounces":
        # Bounces have their own endpoint with subscriber details
        try:
            return await listmonk.get_bounces(page, per_page, campaign_id)
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=str(e))

    query = sql_map.get(engagement_type)
    if not query:
        raise HTTPException(status_code=400, detail=f"Invalid type: {engagement_type}. Use views, clicks, or bounces")

    try:
        return await listmonk.get_subscribers(page, per_page, query)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{campaign_id}/subscribers/{engagement_type}/export")
async def export_campaign_subscribers(campaign_id: int, engagement_type: str):
    """Export subscribers who viewed/clicked/bounced a campaign as CSV."""
    import csv
    import io

    sql_map = {
        "views": f"subscribers.id IN (SELECT subscriber_id FROM campaign_views WHERE campaign_id={campaign_id})",
        "clicks": f"subscribers.id IN (SELECT subscriber_id FROM link_clicks WHERE campaign_id={campaign_id})",
    }

    try:
        if engagement_type == "bounces":
            all_records = []
            page = 1
            while True:
                result = await listmonk.get_bounces(page, 500, campaign_id)
                data = result.get("data", {})
                results = data.get("results", [])
                if not results:
                    break
                all_records.extend(results)
                if page * 500 >= data.get("total", 0):
                    break
                page += 1

            if not all_records:
                raise HTTPException(status_code=404, detail="No bounce records found")

            output = io.StringIO()
            columns = ["email", "type", "source", "created_at"]
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_records)

            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_bounced.csv"},
            )

        query = sql_map.get(engagement_type)
        if not query:
            raise HTTPException(status_code=400, detail=f"Invalid type: {engagement_type}. Use views, clicks, or bounces")

        all_subscribers = []
        page = 1
        while True:
            result = await listmonk.get_subscribers(page, 500, query)
            data = result.get("data", {})
            results = data.get("results", [])
            if not results:
                break
            for sub in results:
                sub["lists"] = ", ".join(l.get("name", "") for l in sub.get("lists", []))
                attribs = sub.get("attribs", {})
                if isinstance(attribs, dict):
                    sub["attribs"] = json.dumps(attribs, ensure_ascii=False) if attribs else ""
            all_subscribers.extend(results)
            if page * 500 >= data.get("total", 0):
                break
            page += 1

        if not all_subscribers:
            raise HTTPException(status_code=404, detail=f"No {engagement_type} subscribers found")

        output = io.StringIO()
        columns = ["id", "email", "name", "status", "lists", "attribs", "created_at"]
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_subscribers)

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_{engagement_type}.csv"},
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: int):
    try:
        return await listmonk.get_campaign(campaign_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{campaign_id}/preview")
async def preview_campaign(campaign_id: int):
    try:
        resp = await listmonk.preview_campaign(campaign_id)
        return HTMLResponse(content=resp.text)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.post("")
async def create_campaign(data: dict):
    try:
        return await listmonk.create_campaign(data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.post("/{campaign_id}/test")
async def test_campaign(campaign_id: int, data: dict):
    try:
        return await listmonk.test_campaign(campaign_id, data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{campaign_id}")
async def update_campaign(campaign_id: int, data: dict):
    try:
        return await listmonk.update_campaign(campaign_id, data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{campaign_id}/status")
async def change_campaign_status(campaign_id: int, data: dict):
    try:
        return await listmonk.change_campaign_status(campaign_id, data.get("status", ""))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{campaign_id}/archive")
async def archive_campaign(campaign_id: int):
    try:
        return await listmonk.archive_campaign(campaign_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int):
    try:
        return await listmonk.delete_campaign(campaign_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
