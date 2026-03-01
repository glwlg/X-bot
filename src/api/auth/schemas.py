"""
用户相关 Schema
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi_users import schemas

from src.api.auth.models import UserRole


class UserRead(schemas.BaseUser[int]):
    """用户读取模型"""

    username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: UserRole = UserRole.VIEWER
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class UserCreate(schemas.BaseUserCreate):
    """用户创建模型"""

    username: Optional[str] = None
    display_name: Optional[str] = None
    role: UserRole = UserRole.VIEWER


class UserUpdate(schemas.BaseUserUpdate):
    """用户更新模型"""

    username: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[UserRole] = None
