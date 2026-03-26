from __future__ import annotations

from api.auth.models import User, UserRole
from core.state_store import add_allowed_user, remove_allowed_user


def _role_text(user: User) -> str:
    role = getattr(user, "role", "")
    if isinstance(role, UserRole):
        return role.value
    return str(role or "").strip().lower()


async def sync_user_core_access(user: User | None, *, actor: str = "") -> None:
    if user is None:
        return

    user_id = str(getattr(user, "id", "") or "").strip()
    if not user_id:
        return

    if not bool(getattr(user, "is_active", False)):
        await remove_allowed_user(user_id)
        return

    role = _role_text(user) or "viewer"
    await add_allowed_user(
        user_id,
        added_by=str(actor or "").strip() or "api_auth",
        description=f"web:{role}",
    )
