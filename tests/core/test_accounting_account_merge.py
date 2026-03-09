from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.api.accounting_router as accounting_router_module
from api.auth.models import User
from api.core.database import Base
from api.models.accounting import Account, AccountAlias, Book, Record, ScheduledTask


@pytest.fixture
async def accounting_session(tmp_path):
    db_path = tmp_path / "accounting-merge.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


async def _create_user_and_book(session):
    user = User(
        email="merge-test@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()

    book = Book(name="测试账本", owner_id=user.id)
    session.add(book)
    await session.flush()
    return user, book


@pytest.mark.asyncio
async def test_get_or_create_account_matches_alias(accounting_session):
    user, book = await _create_user_and_book(accounting_session)
    _ = user

    account = Account(book_id=book.id, name="招商银行信用卡", type="信用卡", balance=0)
    accounting_session.add(account)
    await accounting_session.flush()

    accounting_session.add(
        AccountAlias(
            book_id=book.id,
            account_id=account.id,
            name="招商银行信用卡(0890)",
        )
    )
    await accounting_session.flush()

    resolved = await accounting_router_module._get_or_create_account(
        accounting_session,
        book.id,
        "  招商银行信用卡(0890)  ",
    )

    assert resolved is not None
    assert resolved.id == account.id

    result = await accounting_session.execute(
        select(Account).where(Account.book_id == book.id)
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_merge_account_moves_records_tasks_and_aliases(accounting_session):
    user, book = await _create_user_and_book(accounting_session)

    target = Account(book_id=book.id, name="招商银行信用卡", type="信用卡", balance=100)
    source = Account(book_id=book.id, name="招商银行信用卡(0890)", type="信用卡", balance=20)
    accounting_session.add_all([target, source])
    await accounting_session.flush()

    accounting_session.add(
        AccountAlias(
            book_id=book.id,
            account_id=source.id,
            name="招商银行Visa",
        )
    )
    accounting_session.add_all(
        [
            Record(
                book_id=book.id,
                type="支出",
                amount=10,
                account_id=source.id,
                category_id=None,
                record_time=datetime.now(UTC),
                payee="测试商户",
                remark="源账户支出",
                creator_id=user.id,
            ),
            Record(
                book_id=book.id,
                type="转账",
                amount=5,
                account_id=target.id,
                target_account_id=source.id,
                category_id=None,
                record_time=datetime.now(UTC),
                payee="",
                remark="转入源账户",
                creator_id=user.id,
            ),
            ScheduledTask(
                book_id=book.id,
                name="每月还款",
                frequency="每月",
                next_run=datetime.now(UTC),
                type="转账",
                amount=30,
                account_id=source.id,
                target_account_id=target.id,
                category_id=None,
                payee="",
                remark="周期计划",
                is_active=True,
                creator_id=user.id,
            ),
        ]
    )
    await accounting_session.commit()

    result = await accounting_router_module.merge_account(
        source.id,
        accounting_router_module.AccountMerge(target_account_id=target.id),
        user=user,
        session=accounting_session,
    )

    merged_account = await accounting_session.get(Account, target.id)
    removed_account = await accounting_session.get(Account, source.id)
    aliases = await accounting_session.execute(
        select(AccountAlias.name)
        .where(AccountAlias.account_id == target.id)
        .order_by(AccountAlias.name)
    )
    records = await accounting_session.execute(
        select(Record).order_by(Record.id)
    )
    tasks = await accounting_session.execute(select(ScheduledTask))

    assert removed_account is None
    assert merged_account is not None
    assert float(merged_account.balance) == 120.0
    assert result["message"] == "账户已合并"
    assert result["account"]["name"] == "招商银行信用卡"
    assert result["account"]["initial_balance"] == 120.0
    assert result["account"]["aliases"] == ["招商银行Visa", "招商银行信用卡(0890)"]
    assert aliases.scalars().all() == ["招商银行Visa", "招商银行信用卡(0890)"]

    record_list = records.scalars().all()
    assert record_list[0].account_id == target.id
    assert record_list[1].target_account_id == target.id

    scheduled_task = tasks.scalars().one()
    assert scheduled_task.account_id == target.id
    assert scheduled_task.target_account_id == target.id
