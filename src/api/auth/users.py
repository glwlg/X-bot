"""
fastapi-users 配置

包含用户管理器、认证策略、依赖注入等
"""

from datetime import datetime
from typing import Optional, AsyncGenerator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.core.database import get_async_session
from src.api.auth.models import User, OAuthAccount
from src.api.core.config import settings


# ============== 用户数据库适配器 ==============


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


# ============== 用户管理器 ==============


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    reset_password_token_secret = settings.auth.secret_key
    verification_token_secret = settings.auth.secret_key

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} ({user.email}) has registered.")

    async def on_after_login(
        self,
        user: User,
        request: Optional[Request] = None,
        response=None,
    ):
        """登录后更新最后登录时间"""
        # 注意：需要通过 session 更新
        print(f"User {user.id} ({user.email}) logged in.")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"Verification requested for user {user.id}. Verification token: {token}")


async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db)


# ============== 认证策略 ==============

bearer_transport = BearerTransport(tokenUrl="/ops/api/v1/auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.auth.secret_key,
        lifetime_seconds=settings.auth.access_token_expire_minutes * 60,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


# ============== FastAPIUsers 实例 ==============

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])


# ============== 依赖注入 ==============

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
optional_current_user = fastapi_users.current_user(active=True, optional=True)
