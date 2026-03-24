from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.api.binding_helpers import get_primary_platform_user_id
from api.auth.models import User
from api.auth.users import current_active_user
from api.core.database import get_async_session
from core import state_store

router = APIRouter()
REMOVED_SUBSCRIPTION_FIELDS = {"kind", "provider", "query", "scope"}
REMOVED_SUBSCRIPTION_MESSAGE = "关键词监控已下线，仅支持 RSS 订阅"


class SubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    feed_url: str
    platform: str | None = None


class SubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    feed_url: str | None = None
    platform: str | None = None

def _validate_removed_subscription_fields(payload: dict[str, Any]) -> None:
    for field in REMOVED_SUBSCRIPTION_FIELDS:
        if str(payload.get(field) or "").strip():
            raise HTTPException(status_code=400, detail=REMOVED_SUBSCRIPTION_MESSAGE)


async def _parse_subscription_payload(
    request: Request,
    *,
    partial: bool,
) -> SubscriptionCreate | SubscriptionUpdate:
    try:
        raw_payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    if not isinstance(raw_payload, dict):
        raise HTTPException(status_code=400, detail="RSS payload must be a JSON object")

    _validate_removed_subscription_fields(raw_payload)

    try:
        if partial:
            return SubscriptionUpdate.model_validate(raw_payload)
        return SubscriptionCreate.model_validate(raw_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _resolve_platform_uid(user: User, session: AsyncSession) -> str:
    platform_uid = await get_primary_platform_user_id(user.id, session)
    if not platform_uid:
        raise HTTPException(
            status_code=400,
            detail="No platform binding found. Please bind your Telegram account first.",
        )
    return platform_uid


@router.get("")
async def get_rss(
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    return await state_store.list_subscriptions(platform_uid)


@router.post("")
async def create_rss(
    request: Request,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    sub = await _parse_subscription_payload(request, partial=False)
    try:
        created = await state_store.create_subscription(
            platform_uid,
            sub.model_dump(exclude_none=True),
        )
        return {"success": True, "data": created}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{sub_id}")
async def delete_rss(
    sub_id: int,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    try:
        deleted = await state_store.delete_subscription(platform_uid, sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{sub_id}")
async def update_rss(
    sub_id: int,
    request: Request,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    sub = await _parse_subscription_payload(request, partial=True)
    try:
        ok = await state_store.update_subscription(
            sub_id,
            platform_uid,
            sub.model_dump(exclude_none=True),
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Subscription not found")
        updated = await state_store.get_subscription(platform_uid, sub_id)
        return {"success": True, "data": updated}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
