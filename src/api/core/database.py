"""
SQLite 数据库连接管理模块
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

_engine: AsyncEngine | None = None
_async_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        db_url = "sqlite+aiosqlite:///data/bot_data.db"
        _engine = create_async_engine(
            db_url,
            echo=False,
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _async_session_maker
    if _async_session_maker is None:
        engine = get_engine()
        _async_session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_maker


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    engine = get_engine()
    from api.auth.models import User, OAuthAccount  # noqa: F401
    from api.models.binding import PlatformUserBinding  # noqa: F401
    from api.models.accounting import (  # noqa: F401
        Book,
        Account,
        AccountAlias,
        Category,
        Record,
        Budget,
        ScheduledTask,
        DebtOrReimbursement,
        StatsPanel,
        OperationLog,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
