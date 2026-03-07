"""Helper to resolve web user -> platform user IDs via binding table."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.binding import PlatformUserBinding


async def get_platform_user_ids(
    user_id: int, session: AsyncSession, platform: str = "telegram"
) -> list[str]:
    """Get all platform user IDs bound to a web user for a given platform."""
    result = await session.execute(
        select(PlatformUserBinding.platform_user_id).where(
            PlatformUserBinding.user_id == user_id,
            PlatformUserBinding.platform == platform,
        )
    )
    return [row[0] for row in result.all()]


async def get_primary_platform_user_id(
    user_id: int, session: AsyncSession, platform: str = "telegram"
) -> str | None:
    """Get the primary (first) platform user ID for a web user."""
    ids = await get_platform_user_ids(user_id, session, platform)
    return ids[0] if ids else None
