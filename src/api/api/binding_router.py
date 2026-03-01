from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from api.core.database import get_async_session
from api.auth.users import current_active_user
from api.auth.models import User
from api.models.binding import PlatformUserBinding

router = APIRouter()


class BindingCreate(BaseModel):
    platform: str  # e.g., 'telegram', 'discord'
    platform_user_id: str  # e.g., '257675041'


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
        {
            "id": b.id,
            "platform": b.platform,
            "platform_user_id": b.platform_user_id,
        }
        for b in bindings
    ]


@router.post("/me")
async def create_binding(
    binding: BindingCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Bind a platform account to the current web user."""
    platform = binding.platform.strip().lower()
    platform_user_id = binding.platform_user_id.strip()

    if not platform or not platform_user_id:
        raise HTTPException(
            status_code=400, detail="platform and platform_user_id are required"
        )

    # Check if this platform_user_id is already bound to another web user
    existing = await session.execute(
        select(PlatformUserBinding).where(
            PlatformUserBinding.platform == platform,
            PlatformUserBinding.platform_user_id == platform_user_id,
        )
    )
    existing_binding = existing.scalar_one_or_none()
    if existing_binding:
        if existing_binding.user_id == user.id:
            return {"success": True, "message": "Already bound"}
        raise HTTPException(
            status_code=409,
            detail=f"This {platform} account is already bound to another user",
        )

    # Check if user already has a binding for this platform
    user_existing = await session.execute(
        select(PlatformUserBinding).where(
            PlatformUserBinding.user_id == user.id,
            PlatformUserBinding.platform == platform,
        )
    )
    user_existing_binding = user_existing.scalar_one_or_none()
    if user_existing_binding:
        # Update existing binding
        user_existing_binding.platform_user_id = platform_user_id
        await session.commit()
        return {"success": True, "message": "Binding updated"}

    # Create new binding
    new_binding = PlatformUserBinding(
        user_id=user.id,
        platform=platform,
        platform_user_id=platform_user_id,
    )
    session.add(new_binding)
    await session.commit()
    return {"success": True, "message": "Binding created"}


@router.delete("/me/{binding_id}")
async def delete_binding(
    binding_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Remove a platform binding."""
    result = await session.execute(
        select(PlatformUserBinding).where(
            PlatformUserBinding.id == binding_id,
            PlatformUserBinding.user_id == user.id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        raise HTTPException(status_code=404, detail="Binding not found")

    await session.delete(binding)
    await session.commit()
    return {"success": True}
