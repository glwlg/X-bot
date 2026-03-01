from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.users import current_active_user
from api.auth.models import User
from api.core.database import get_async_session
from api.api.binding_helpers import get_primary_platform_user_id
from core import state_store

router = APIRouter()


class TaskCreate(BaseModel):
    crontab: str
    instruction: str


class TaskStatusUpdate(BaseModel):
    is_active: bool


async def _resolve_platform_uid(user: User, session: AsyncSession) -> str:
    """Resolve web user to platform user ID, raise 400 if not bound."""
    platform_uid = await get_primary_platform_user_id(user.id, session)
    if not platform_uid:
        raise HTTPException(
            status_code=400,
            detail="No platform binding found. Please bind your Telegram account first.",
        )
    return platform_uid


@router.get("")
async def get_tasks(
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    return await state_store.get_all_active_tasks(platform_uid)


@router.post("")
async def create_task(
    task: TaskCreate,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    try:
        await state_store.add_scheduled_task(
            task.crontab, task.instruction, platform_uid
        )
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    try:
        await state_store.delete_task(task_id, platform_uid)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{task_id}/status")
async def update_task_status(
    task_id: int,
    status: TaskStatusUpdate,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    try:
        await state_store.update_task_status(task_id, status.is_active, platform_uid)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
