"""用户相关 Schema"""

from typing import Optional
from datetime import datetime
from fastapi_users import schemas
from pydantic import BaseModel, ConfigDict

from api.auth.models import UserRole


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


class UserBootstrapCreate(schemas.BaseUserCreate):
    """首个管理员初始化模型"""

    username: Optional[str] = None
    display_name: Optional[str] = None


class UserSelfUpdate(BaseModel):
    """当前用户可更新字段"""

    model_config = ConfigDict(extra="forbid")

    username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    password: Optional[str] = None


class UserAdminCreate(schemas.BaseUserCreate):
    """管理员创建用户模型"""

    username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: UserRole = UserRole.VIEWER
    is_active: bool = True
    is_verified: bool = True


class UserAdminUpdate(BaseModel):
    """管理员更新用户模型"""

    model_config = ConfigDict(extra="forbid")

    username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    password: Optional[str] = None


class BootstrapStatus(BaseModel):
    needs_bootstrap: bool
    users_count: int
    admin_count: int
    public_registration_enabled: bool


class TtsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: str = "alloy"
    message_id: str


class WebSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    preferences: Optional[dict[str, Any]] = None


class WebInboundEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    text: Optional[str] = None
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    caption: Optional[str] = None
    callback_data: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
