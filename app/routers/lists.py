from fastapi import APIRouter
from app.services.listmonk_client import listmonk

router = APIRouter()


@router.get("")
async def get_lists(page: int = 1, per_page: int = 50,
                    query: str = "", status: str = "",
                    order_by: str = "created_at", order: str = "DESC",
                    minimal: bool = False):
    return await listmonk.get_lists(page, per_page, query, status,
                                    order_by, order, minimal)


@router.get("/{list_id}")
async def get_list(list_id: int):
    return await listmonk.get_list(list_id)


@router.post("")
async def create_list(data: dict):
    return await listmonk.create_list(data)


@router.put("/{list_id}")
async def update_list(list_id: int, data: dict):
    return await listmonk.update_list(list_id, data)


@router.delete("/{list_id}")
async def delete_list(list_id: int):
    return await listmonk.delete_list(list_id)
