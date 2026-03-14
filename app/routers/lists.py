from fastapi import APIRouter, HTTPException
from typing import Optional
from app.services.listmonk_client import listmonk
import httpx

router = APIRouter()


@router.get("")
async def get_lists(page: int = 1, per_page: int = 50,
                    query: str = "", status: str = "",
                    order_by: str = "created_at", order: str = "DESC",
                    minimal: bool = False):
    try:
        return await listmonk.get_lists(page, per_page, query, status,
                                        order_by, order, minimal)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/{list_id}")
async def get_list(list_id: int):
    try:
        return await listmonk.get_list(list_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.post("")
async def create_list(data: dict):
    try:
        return await listmonk.create_list(data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.put("/{list_id}")
async def update_list(list_id: int, data: dict):
    try:
        return await listmonk.update_list(list_id, data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.delete("/{list_id}")
async def delete_list(list_id: int):
    try:
        return await listmonk.delete_list(list_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
