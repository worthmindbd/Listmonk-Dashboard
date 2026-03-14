from fastapi import APIRouter, HTTPException
from app.services.listmonk_client import listmonk
import httpx

router = APIRouter()


@router.get("")
async def get_templates():
    try:
        return await listmonk.get_templates()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{template_id}")
async def get_template(template_id: int):
    try:
        return await listmonk.get_template(template_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.post("")
async def create_template(data: dict):
    try:
        return await listmonk.create_template(data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{template_id}")
async def update_template(template_id: int, data: dict):
    try:
        return await listmonk.update_template(template_id, data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{template_id}/default")
async def set_default_template(template_id: int):
    try:
        return await listmonk.set_default_template(template_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.delete("/{template_id}")
async def delete_template(template_id: int):
    try:
        return await listmonk.delete_template(template_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
