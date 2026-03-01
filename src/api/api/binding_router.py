from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from api.core.database import get_async_session
from api.auth.users import current_active_user
from api.auth.models import User
from api.models.binding import PlatformUserBinding

router = APIRouter()


@router.get("/me")
async def get_my_bindings(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Get all platform bindings for the current user."""
    result = await session.execute(
        select(PlatformUserBinding).where(PlatformUserBinding.user_id == user.id)
    )
    bindings = result.scalars().all()
    return [
        {"platform": b.platform, "platform_user_id": b.platform_user_id}
        for b in bindings
    ]
