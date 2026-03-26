"""认证与用户管理路由。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users.exceptions import UserAlreadyExists
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.users import (
    fastapi_users,
    auth_backend,
    current_active_user,
    get_user_manager,
)
from api.auth.schemas import (
    BootstrapStatus,
    UserAdminCreate,
    UserAdminUpdate,
    UserBootstrapCreate,
    UserRead,
    UserSelfUpdate,
)
from api.auth.models import User, UserRole
from api.core.database import get_async_session
from api.services.admin_audit import record_admin_audit
from api.services.bootstrap_admin import count_admin_users, ensure_bootstrap_admin
from api.services.env_config import ensure_admin_user_id_present
from api.services.user_access_sync import sync_user_core_access
from core.runtime_config_store import runtime_config_store

router = APIRouter()

# ============== JWT 认证路由 ==============
# POST /auth/jwt/login - 登录获取 token
# POST /auth/jwt/logout - 登出
router.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/jwt", tags=["auth"]
)


# ============== 自定义用户端点 ==============


@router.get("/me", response_model=UserRead, tags=["auth"])
async def get_current_user_info(user: User = Depends(current_active_user)):
    """获取当前登录用户信息"""
    await sync_user_core_access(user, actor="auth_me")
    return user


@router.get("/me/permissions", tags=["auth"])
async def get_current_user_permissions(user: User = Depends(current_active_user)):
    """获取当前用户权限"""
    return {
        "role": user.role,
        "is_admin": user.role == UserRole.ADMIN,
        "is_operator": user.has_permission(UserRole.OPERATOR),
        "is_superuser": user.is_superuser,
    }


async def _counts(session: AsyncSession) -> tuple[int, int]:
    users_total = await session.execute(select(func.count(User.id)))
    admins_total = await session.execute(
        select(func.count(User.id)).where(User.role == UserRole.ADMIN)
    )
    return int(users_total.scalar() or 0), int(admins_total.scalar() or 0)


def _client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is None:
        return ""
    return str(request.client.host or "").strip()


def _actor_label(user: User | None) -> str:
    if user is None:
        return "anonymous"
    return f"{user.id}:{user.email}"


def require_role(required_role: UserRole):
    """创建角色检查依赖"""

    async def check_role(user: User = Depends(current_active_user)):
        if not user.has_permission(required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {required_role.value} 或更高权限",
            )
        return user

    return check_role


require_admin = require_role(UserRole.ADMIN)
require_operator = require_role(UserRole.OPERATOR)
require_viewer = require_role(UserRole.VIEWER)


async def _create_user_from_payload(
    *,
    payload: UserBootstrapCreate | UserAdminCreate,
    role: UserRole,
    manager: Any,
    session: AsyncSession,
    request: Request | None = None,
) -> User:
    created = await manager.create(payload, safe=False, request=request)
    created.role = role
    created.is_superuser = role == UserRole.ADMIN
    created.is_active = bool(getattr(payload, "is_active", True))
    created.is_verified = bool(getattr(payload, "is_verified", True))
    session.add(created)
    await session.flush()
    await session.refresh(created)
    return created


@router.get("/bootstrap/status", response_model=BootstrapStatus, tags=["auth"])
async def bootstrap_status(
    session: AsyncSession = Depends(get_async_session),
):
    await ensure_bootstrap_admin(session, reason="bootstrap_status")
    users_count, admin_count = await _counts(session)
    return BootstrapStatus(
        needs_bootstrap=admin_count == 0,
        users_count=users_count,
        admin_count=admin_count,
        public_registration_enabled=runtime_config_store.get_public_registration_enabled(),
    )


@router.post("/bootstrap/admin", response_model=UserRead, tags=["auth"])
async def bootstrap_admin(
    payload: UserBootstrapCreate,
    request: Request,
    manager=Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
):
    await ensure_bootstrap_admin(session, reason="bootstrap_admin_precheck")
    users_count, admin_count = await _counts(session)
    if admin_count > 0:
        raise HTTPException(status_code=403, detail="管理员已初始化")
    try:
        user = await _create_user_from_payload(
            payload=payload,
            role=UserRole.ADMIN,
            manager=manager,
            session=session,
            request=request,
        )
    except UserAlreadyExists as exc:
        raise HTTPException(status_code=400, detail="用户已存在") from exc

    await record_admin_audit(
        {
            "action": "bootstrap_admin",
            "actor": "bootstrap",
            "target": f"user:{user.id}",
            "summary": f"created first admin {user.email}; users_before={users_count}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    ensure_admin_user_id_present(user.id, actor="bootstrap", reason="bootstrap_admin")
    await sync_user_core_access(user, actor="bootstrap_admin")
    return user


@router.patch("/me/profile", response_model=UserRead, tags=["auth"])
async def update_my_profile(
    payload: UserSelfUpdate,
    user: User = Depends(current_active_user),
    manager=Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
):
    data = payload.model_dump(exclude_none=True)
    if "password" in data:
        raw_password = str(data.pop("password") or "").strip()
        if raw_password:
            user.hashed_password = manager.password_helper.hash(raw_password)
    for field in ("username", "display_name", "avatar_url"):
        if field in data:
            setattr(user, field, data[field])
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


@router.get("/users", response_model=list[UserRead], tags=["users"])
async def list_users(
    _: User = Depends(require_operator),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(User).order_by(User.created_at.desc(), User.id.desc()))
    return list(result.unique().scalars().all())


@router.post("/users", response_model=UserRead, tags=["users"])
async def create_user(
    payload: UserAdminCreate,
    request: Request,
    admin_user: User = Depends(require_admin),
    manager=Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        created = await _create_user_from_payload(
            payload=payload,
            role=payload.role,
            manager=manager,
            session=session,
            request=request,
        )
    except UserAlreadyExists as exc:
        raise HTTPException(status_code=400, detail="用户已存在") from exc

    await record_admin_audit(
        {
            "action": "create_user",
            "actor": _actor_label(admin_user),
            "target": f"user:{created.id}",
            "summary": f"created user {created.email} role={created.role}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    if created.role == UserRole.ADMIN:
        ensure_admin_user_id_present(
            created.id,
            actor=_actor_label(admin_user),
            reason="create_admin_user",
        )
    await sync_user_core_access(created, actor=_actor_label(admin_user))
    return created


@router.patch("/users/{user_id}", response_model=UserRead, tags=["users"])
async def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    request: Request,
    admin_user: User = Depends(require_admin),
    manager=Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(User).where(User.id == user_id))
    target = result.unique().scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    data = payload.model_dump(exclude_none=True)
    next_role = data.get("role") if data.get("role") is not None else target.role
    next_is_active = bool(data.get("is_active")) if "is_active" in data else bool(target.is_active)

    if target.id == admin_user.id and next_role != UserRole.ADMIN:
        raise HTTPException(status_code=400, detail="不能将当前管理员自身降级")
    if target.id == admin_user.id and not next_is_active:
        raise HTTPException(status_code=400, detail="不能停用当前管理员自身")

    if target.role == UserRole.ADMIN and next_role != UserRole.ADMIN:
        remaining_admins = await count_admin_users(session, exclude_user_id=target.id)
        if remaining_admins == 0:
            raise HTTPException(status_code=400, detail="至少保留一个管理员")

    if target.role == UserRole.ADMIN and not next_is_active:
        remaining_active_admins = await count_admin_users(
            session,
            exclude_user_id=target.id,
            active_only=True,
        )
        if remaining_active_admins == 0:
            raise HTTPException(status_code=400, detail="至少保留一个启用中的管理员")

    if "password" in data:
        raw_password = str(data.pop("password") or "").strip()
        if raw_password:
            target.hashed_password = manager.password_helper.hash(raw_password)
    if "role" in data and data["role"] is not None:
        target.role = data["role"]
        target.is_superuser = target.role == UserRole.ADMIN
    for field in ("username", "display_name", "avatar_url", "is_active", "is_verified"):
        if field in data:
            setattr(target, field, data[field])

    session.add(target)
    await session.flush()
    await session.refresh(target)
    await record_admin_audit(
        {
            "action": "update_user",
            "actor": _actor_label(admin_user),
            "target": f"user:{target.id}",
            "summary": f"updated user {target.email}",
            "ip": _client_ip(request),
            "status": "success",
        }
    )
    if target.role == UserRole.ADMIN:
        ensure_admin_user_id_present(
            target.id,
            actor=_actor_label(admin_user),
            reason="update_admin_user",
        )
    await sync_user_core_access(target, actor=_actor_label(admin_user))
    return target


