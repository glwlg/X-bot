import logging
from typing import Dict, Any
from datetime import datetime

from core.platform.models import UnifiedContext
from sqlalchemy import select

from core.accounting_store import get_active_book_id

# 必须通过 src 引用我们的 DB 和 Models
from api.core.database import get_session_maker
from api.models.binding import PlatformUserBinding
from api.models.accounting import Book, Account, Category, Record

logger = logging.getLogger(__name__)


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> Dict[str, Any]:
    """Execute quick accounting from parsed LLM parameters."""
    platform = ctx.message.platform or "telegram"
    platform_user_id = str(ctx.message.user.id)

    rtype = params.get("type", "支出")
    try:
        amount = float(params.get("amount", 0.0))
    except ValueError, TypeError:
        return {"text": f"❌ 金额解析错误，参数: {params.get('amount')}", "ui": {}}

    category_name = str(params.get("category", "未分类")).strip()
    account_name = str(params.get("account", "")).strip()
    target_account_name = str(params.get("target_account", "")).strip()
    payee = str(params.get("payee", "")).strip()
    remark = str(params.get("remark", "")).strip()
    record_time_str = str(params.get("record_time", "")).strip()

    if not record_time_str or record_time_str == "None":
        record_time = datetime.utcnow()
    else:
        try:
            # 兼容 "YYYY-MM-DD HH:MM:SS"
            record_time = datetime.strptime(record_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            record_time = datetime.utcnow()

    session_maker = get_session_maker()
    async with session_maker() as session:
        # Check binding
        stmt = select(PlatformUserBinding).where(
            PlatformUserBinding.platform == platform,
            PlatformUserBinding.platform_user_id == platform_user_id,
        )
        binding = (await session.execute(stmt)).scalars().first()

        if not binding:
            return {
                "text": f"❌ 您未绑定账号，请先在网页端绑定（您的 ID：`{platform_user_id}`, 平台：`{platform}`）。",
                "ui": {},
            }

        user_id = binding.user_id

        # Get Book
        active_book_id = await get_active_book_id(user_id)

        stmt = select(Book).where(Book.owner_id == user_id)
        books = (await session.execute(stmt)).scalars().all()

        if not books:
            return {
                "text": "❌ 您还未创建任何账本，请先在系统内创建一个账本。",
                "ui": {},
            }

        book = None
        if active_book_id is not None:
            book = next((b for b in books if b.id == active_book_id), None)

        if not book:
            book = books[0]

        # Helper logic to get or create
        async def get_or_create_account(name: str):
            if not name:
                return None
            res = await session.execute(
                select(Account).where(Account.book_id == book.id, Account.name == name)
            )
            acc = res.scalars().first()
            if not acc:
                acc = Account(book_id=book.id, name=name)
                session.add(acc)
                await session.flush()
            return acc

        async def get_or_create_category(name: str, ctype: str):
            if not name:
                return None
            res = await session.execute(
                select(Category).where(
                    Category.book_id == book.id,
                    Category.name == name,
                    Category.type == ctype,
                )
            )
            cat = res.scalars().first()
            if not cat:
                cat = Category(book_id=book.id, name=name, type=ctype)
                session.add(cat)
                await session.flush()
            return cat

        # Actually create the records
        from_acc = await get_or_create_account(account_name)
        to_acc = (
            await get_or_create_account(target_account_name)
            if rtype == "转账"
            else None
        )
        cat = await get_or_create_category(category_name, rtype)

        rec = Record(
            book_id=book.id,
            type=rtype,
            amount=amount,
            account_id=from_acc.id if from_acc else None,
            target_account_id=to_acc.id if to_acc else None,
            category_id=cat.id if cat else None,
            record_time=record_time,
            payee=payee[:100],
            remark=remark[:500],
            creator_id=user_id,
        )
        session.add(rec)
        await session.commit()

    return {
        "text": f"✅ 已成功记入账本 `{book.name}`！\n类型：{rtype} {amount} 元\n分类：{category_name}\n账户：{account_name}\n商家：{payee}\n备注：{remark}",
        "ui": {},
    }
