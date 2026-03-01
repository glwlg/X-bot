"""
FastAPI 认证依赖项

用于路由中进行权限验证，使用 JWT Token 认证
"""

from typing import Optional
from fastapi import Depends, HTTPException, status, Header
import jwt

from src.api.core.config import settings


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """
    获取当前用户信息（从 JWT Token 验证）

    Args:
        authorization: Authorization header (Bearer token)

    Returns:
        用户信息字典

    Raises:
        HTTPException: 未授权或Token无效
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 提取Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证格式",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]

    try:
        # 解码并验证 JWT token
        payload = jwt.decode(
            token,
            settings.auth.secret_key,
            algorithms=["HS256"],
            audience=["fastapi-users:auth"],
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的Token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 从数据库获取用户信息
        from src.api.core.database import get_session_maker
        from src.api.auth.models import User
        from sqlalchemy import select

        session_maker = get_session_maker()
        async with session_maker() as session:
            result = await session.execute(select(User).where(User.id == int(user_id)))
            user = result.unique().scalar_one_or_none()

            if not user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="用户不存在",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="用户已被禁用",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            return {
                "id": user.id,
                "username": user.username or user.email,
                "nickname": user.display_name or user.username or user.email,
                "email": user.email,
                "role": user.role.value if user.role else "viewer",
                "is_admin": user.is_superuser
                or (user.role and user.role.value == "admin"),
                "is_superuser": user.is_superuser,
            }

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"无效的Token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(
    authorization: Optional[str] = Header(None),
) -> Optional[dict]:
    """
    可选的用户认证（不强制要求登录）

    Args:
        authorization: Authorization header (Bearer token)

    Returns:
        用户信息字典或None
    """
    if not authorization:
        return None

    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None


async def verify_login(current_user: dict = Depends(get_current_user)) -> dict:
    """验证用户已登录 (Viewer权限)"""
    return current_user


async def verify_operator(current_user: dict = Depends(get_current_user)) -> dict:
    """
    验证运维人员权限 (Operator)
    - Admin 和 Operator 可以访问
    - Viewer 禁止访问
    """
    role = current_user.get("role")
    is_admin = current_user.get("is_admin", False)

    if is_admin or role == "admin" or role == "operator":
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="需要运维人员或以上权限"
    )


async def verify_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """
    验证管理员权限 (Admin)
    - 仅 Admin 可以访问
    """
    is_admin = current_user.get("is_admin", False)

    if is_admin:
        return current_user

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
