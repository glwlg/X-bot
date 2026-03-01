"""
用户和OAuth账号模型

基于 fastapi-users 的 SQLAlchemy 模型
"""

from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Boolean, ForeignKey, DateTime, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from fastapi_users.db import SQLAlchemyBaseUserTable, SQLAlchemyBaseOAuthAccountTable
import enum

from src.api.core.database import Base


class UserRole(str, enum.Enum):
    """用户角色枚举"""

    ADMIN = "admin"  # 管理员：完全权限
    OPERATOR = "operator"  # 运维人员：操作权限
    VIEWER = "viewer"  # 只读用户：查看权限


class OAuthAccount(SQLAlchemyBaseOAuthAccountTable[int], Base):
    """OAuth 账号关联表"""

    __tablename__ = "oauth_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    oauth_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    access_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[Optional[int]] = mapped_column(nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    account_id: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    account_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="oauth_accounts")


class User(SQLAlchemyBaseUserTable[int], Base):
    """用户表"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 扩展字段
    username: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True, nullable=True
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole), default=UserRole.VIEWER, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 关联
    oauth_accounts: Mapped[List[OAuthAccount]] = relationship(
        "OAuthAccount", back_populates="user", lazy="joined"
    )

    def has_permission(self, required_role: UserRole) -> bool:
        """检查用户是否有指定角色或更高权限"""
        role_hierarchy = {UserRole.VIEWER: 1, UserRole.OPERATOR: 2, UserRole.ADMIN: 3}
        return role_hierarchy.get(self.role, 0) >= role_hierarchy.get(required_role, 0)
