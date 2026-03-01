from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.users import current_active_user
from api.auth.models import User
from api.core.database import get_async_session
from api.api.binding_helpers import get_primary_platform_user_id
from core.heartbeat_store import HeartbeatStore

router = APIRouter()
hstore = HeartbeatStore()


class MonitorCreate(BaseModel):
    item: str


async def _resolve_platform_uid(user: User, session: AsyncSession) -> str:
    """Resolve web user to platform user ID, raise 400 if not bound."""
    platform_uid = await get_primary_platform_user_id(user.id, session)
    if not platform_uid:
        raise HTTPException(
            status_code=400,
            detail="No platform binding found. Please bind your Telegram account first.",
        )
    return platform_uid


@router.get("/")
async def get_monitors(
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    return await hstore.list_checklist(platform_uid)


@router.post("/")
async def create_monitor(
    monitor: MonitorCreate,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    try:
        await hstore.add_checklist_item(platform_uid, monitor.item)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{index}")
async def delete_monitor(
    index: int,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    try:
        await hstore.remove_checklist_item(platform_uid, index)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
