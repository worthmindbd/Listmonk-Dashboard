import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional
from app.services.listmonk_client import listmonk
from app.services.export_service import dict_list_to_csv

router = APIRouter()


def _engagement_query(campaign_id: int, engagement_type: str) -> str | None:
    return {
        "views": f"subscribers.id IN (SELECT subscriber_id FROM campaign_views WHERE campaign_id={campaign_id})",
        "clicks": f"subscribers.id IN (SELECT subscriber_id FROM link_clicks WHERE campaign_id={campaign_id})",
    }.get(engagement_type)


@router.get("")
async def get_campaigns(page: int = 1, per_page: int = 50,
                        query: str = "", status: str = "",
                        order_by: str = "created_at", order: str = "DESC"):
    return await listmonk.get_campaigns(page, per_page, query, status,
                                        order_by, order)


@router.get("/running/stats")
async def get_running_stats(campaign_id: Optional[int] = None):
    return await listmonk.get_running_stats(campaign_id)


@router.get("/analytics/{analytics_type}")
async def get_campaign_analytics(analytics_type: str,
                                 campaign_id: int = 0,
                                 from_date: str = "", to_date: str = ""):
    return await listmonk.get_campaign_analytics(analytics_type, campaign_id,
                                                 from_date, to_date)


@router.get("/analytics/{analytics_type}/export")
async def export_campaign_analytics(analytics_type: str,
                                    campaign_id: int = 0,
                                    from_date: str = "", to_date: str = ""):
    """Export campaign analytics as CSV."""
    result = await listmonk.get_campaign_analytics(analytics_type, campaign_id,
                                                   from_date, to_date)
    data = result.get("data", [])
    if not data:
        raise HTTPException(status_code=404, detail="No analytics data found")

    columns = list(data[0].keys())
    return StreamingResponse(
        dict_list_to_csv(data, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=analytics_{analytics_type}.csv"},
    )


@router.get("/export-all")
async def export_all_campaigns():
    """Export all campaigns summary as CSV."""
    all_campaigns = await listmonk.paginate_all(
        listmonk.get_campaigns, per_page=100,
    )
    if not all_campaigns:
        raise HTTPException(status_code=404, detail="No campaigns found")

    columns = ["id", "name", "subject", "status", "type", "to_send", "sent",
                "views", "clicks", "bounces", "created_at", "started_at"]
    return StreamingResponse(
        dict_list_to_csv(all_campaigns, columns),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=campaigns_export.csv"},
    )


@router.get("/{campaign_id}/subscribers/{engagement_type}")
async def get_campaign_subscribers(campaign_id: int, engagement_type: str,
                                   page: int = 1, per_page: int = 50):
    """Get subscribers who viewed/clicked/bounced for a campaign."""
    if engagement_type == "bounces":
        return await listmonk.get_bounces(page, per_page, campaign_id)

    query = _engagement_query(campaign_id, engagement_type)
    if not query:
        raise HTTPException(status_code=400, detail=f"Invalid type: {engagement_type}. Use views, clicks, or bounces")

    return await listmonk.get_subscribers(page, per_page, query)


@router.get("/{campaign_id}/subscribers/{engagement_type}/export")
async def export_campaign_subscribers(campaign_id: int, engagement_type: str):
    """Export subscribers who viewed/clicked/bounced a campaign as CSV."""
    if engagement_type == "bounces":
        all_records = await listmonk.paginate_all(
            listmonk.get_bounces, per_page=500, campaign_id=campaign_id,
        )
        if not all_records:
            raise HTTPException(status_code=404, detail="No bounce records found")

        columns = ["email", "type", "source", "created_at"]
        return StreamingResponse(
            dict_list_to_csv(all_records, columns),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_bounced.csv"},
        )

    query = _engagement_query(campaign_id, engagement_type)
    if not query:
        raise HTTPException(status_code=400, detail=f"Invalid type: {engagement_type}. Use views, clicks, or bounces")

    all_subscribers = await listmonk.paginate_all(
        listmonk.get_subscribers, per_page=500, query=query,
    )
    if not all_subscribers:
        raise HTTPException(status_code=404, detail=f"No {engagement_type} subscribers found")

    for sub in all_subscribers:
        sub["lists"] = ", ".join(l.get("name", "") for l in sub.get("lists", []))
        attribs = sub.get("attribs", {})
        if isinstance(attribs, dict):
            sub["attribs"] = json.dumps(attribs, ensure_ascii=False) if attribs else ""

    columns = ["id", "email", "name", "status", "lists", "attribs", "created_at"]
    return StreamingResponse(
        dict_list_to_csv(all_subscribers, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_{engagement_type}.csv"},
    )


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: int):
    return await listmonk.get_campaign(campaign_id)


@router.get("/{campaign_id}/preview")
async def preview_campaign(campaign_id: int):
    resp = await listmonk.preview_campaign(campaign_id)
    return HTMLResponse(content=resp.text)


@router.post("")
async def create_campaign(data: dict):
    return await listmonk.create_campaign(data)


@router.post("/{campaign_id}/test")
async def test_campaign(campaign_id: int, data: dict):
    return await listmonk.test_campaign(campaign_id, data)


@router.put("/{campaign_id}")
async def update_campaign(campaign_id: int, data: dict):
    return await listmonk.update_campaign(campaign_id, data)


@router.put("/{campaign_id}/status")
async def change_campaign_status(campaign_id: int, data: dict):
    return await listmonk.change_campaign_status(campaign_id, data.get("status", ""))


@router.put("/{campaign_id}/archive")
async def archive_campaign(campaign_id: int):
    return await listmonk.archive_campaign(campaign_id)


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int):
    return await listmonk.delete_campaign(campaign_id)
