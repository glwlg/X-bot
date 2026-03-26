from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User, UserRole
from api.services.admin_audit import record_admin_audit


def _needs_admin_repair(user: User) -> bool:
    return user.role != UserRole.ADMIN or not bool(user.is_superuser)


async def ensure_bootstrap_admin(
    session: AsyncSession,
    *,
    reason: str,
) -> User | None:
    result = await session.execute(select(User).order_by(User.created_at.asc(), User.id.asc()))
    users = list(result.unique().scalars().all())
    if not users:
        return None

    admin_like = [user for user in users if user.role == UserRole.ADMIN or bool(user.is_superuser)]
    repaired: list[User] = []

    if admin_like:
        for user in admin_like:
            changed = False
            if user.role != UserRole.ADMIN:
                user.role = UserRole.ADMIN
                changed = True
            if not bool(user.is_superuser):
                user.is_superuser = True
                changed = True
            if changed:
                session.add(user)
                repaired.append(user)
        if repaired:
            await session.flush()
            for user in repaired:
                await session.refresh(user)
                await record_admin_audit(
                    {
                        "action": "repair_admin_consistency",
                        "actor": "system",
                        "target": f"user:{user.id}",
                        "summary": f"normalized admin flags for {user.email}; reason={reason}",
                        "ip": "",
                        "status": "success",
                    }
                )
            return repaired[0]
        return admin_like[0]

    first_user = users[0]
    first_user.role = UserRole.ADMIN
    first_user.is_superuser = True
    first_user.is_active = True
    first_user.is_verified = True
    session.add(first_user)
    await session.flush()
    await session.refresh(first_user)
    await record_admin_audit(
        {
            "action": "repair_bootstrap_admin",
            "actor": "system",
            "target": f"user:{first_user.id}",
            "summary": f"promoted first user {first_user.email} to bootstrap admin; reason={reason}",
            "ip": "",
            "status": "success",
        }
    )
    return first_user


async def count_admin_users(
    session: AsyncSession,
    *,
    exclude_user_id: int | None = None,
    active_only: bool = False,
) -> int:
    result = await session.execute(select(User).where(User.role == UserRole.ADMIN))
    users = list(result.unique().scalars().all())
    count = 0
    for user in users:
        if exclude_user_id is not None and user.id == exclude_user_id:
            continue
        if active_only and not bool(user.is_active):
            continue
        count += 1
    return count
