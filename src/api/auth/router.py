"""
认证路由

包含 JWT 登录、用户管理等路由
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional

from src.api.auth.users import (
    fastapi_users,
    auth_backend,
    current_active_user,
    current_superuser,
)
from src.api.auth.schemas import UserRead, UserCreate, UserUpdate
from src.api.auth.models import User, UserRole

router = APIRouter()

# ============== JWT 认证路由 ==============
# POST /auth/jwt/login - 登录获取 token
# POST /auth/jwt/logout - 登出
router.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/jwt", tags=["auth"]
)


# ============== 用户注册路由 ==============
# POST /auth/register - 用户注册
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate), prefix="", tags=["auth"]
)


# ============== 用户管理路由 ==============
# GET /auth/users/me - 获取当前用户信息
# PATCH /auth/users/me - 更新当前用户信息
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)


# ============== 自定义用户端点 ==============


@router.get("/me", response_model=UserRead, tags=["auth"])
async def get_current_user_info(user: User = Depends(current_active_user)):
    """获取当前登录用户信息"""
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


# ============== 角色检查依赖 ==============


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


# 导出常用依赖
require_admin = require_role(UserRole.ADMIN)
require_operator = require_role(UserRole.OPERATOR)
require_viewer = require_role(UserRole.VIEWER)
