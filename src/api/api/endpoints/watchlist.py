import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.users import current_active_user
from api.auth.models import User
from api.core.database import get_async_session
from api.api.binding_helpers import get_primary_platform_user_id
from extension.skills.learned.stock_watch.scripts import store as stock_watch_store
from extension.skills.learned.stock_watch.scripts.services.stock_service import (
    fetch_stock_quotes,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class StockAdd(BaseModel):
    stock_code: str
    stock_name: str


class StockUpdate(BaseModel):
    stock_code: str
    stock_name: str


async def _resolve_platform_uid(user: User, session: AsyncSession) -> str:
    platform_uid = await get_primary_platform_user_id(user.id, session)
    if not platform_uid:
        raise HTTPException(
            status_code=400,
            detail="No platform binding found. Please bind your Telegram account first.",
        )
    return platform_uid


@router.get("")
async def get_watchlist(
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    stocks = await stock_watch_store.get_user_watchlist(platform_uid)
    if not stocks:
        return []

    # Fetch real-time quotes
    codes = [s["stock_code"] for s in stocks if s.get("stock_code")]
    quotes_map: dict[str, dict] = {}
    try:
        quotes = await fetch_stock_quotes(codes)
        for q in quotes:
            quotes_map[q["code"]] = q
    except Exception as exc:
        logger.warning("Failed to fetch watchlist stock quotes: %s", exc)

    # Merge quotes into stock list
    result = []
    for s in stocks:
        code = s.get("stock_code", "")
        q = quotes_map.get(code, {})
        result.append(
            {
                **s,
                "price": q.get("price", 0),
                "change": q.get("change", 0),
                "percent": q.get("percent", 0),
                "high": q.get("high", 0),
                "low": q.get("low", 0),
                "open": q.get("open", 0),
                "yesterday_close": q.get("yesterday_close", 0),
            }
        )
    return result


@router.post("")
async def add_stock(
    stock: StockAdd,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    code = stock.stock_code.strip()
    name = stock.stock_name.strip()
    if not code:
        raise HTTPException(status_code=400, detail="stock_code is required")
    ok = await stock_watch_store.add_watchlist_stock(platform_uid, code, name)
    if not ok:
        raise HTTPException(status_code=409, detail="Stock already in watchlist")
    return {"success": True}


@router.put("/{stock_code}")
async def update_stock(
    stock_code: str,
    stock: StockUpdate,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    # Remove old, add new
    await stock_watch_store.remove_watchlist_stock(platform_uid, stock_code)
    await stock_watch_store.add_watchlist_stock(
        platform_uid, stock.stock_code.strip(), stock.stock_name.strip()
    )
    return {"success": True}


@router.delete("/{stock_code}")
async def remove_stock(
    stock_code: str,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    platform_uid = await _resolve_platform_uid(current_user, session)
    ok = await stock_watch_store.remove_watchlist_stock(platform_uid, stock_code)
    if not ok:
        raise HTTPException(status_code=404, detail="Stock not found in watchlist")
    return {"success": True}
