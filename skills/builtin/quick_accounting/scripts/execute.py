from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    parse_json_object,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from api.auth.models import User as _AuthUser  # noqa: F401
from api.core.database import get_session_maker
from api.models.accounting import Account, Book, Category, Record
from api.models.binding import PlatformUserBinding
from core.accounting_store import get_active_book_id

logger = logging.getLogger(__name__)
VALID_RECORD_TYPES = {"支出", "收入", "转账"}


def _failure_response(message: str, *, error_code: str) -> Dict[str, Any]:
    text = f"❌ {message}"
    return {
        "success": False,
        "error_code": error_code,
        "text": text,
        "failure_mode": "fatal",
        "terminal": True,
        "task_outcome": "failed",
        "payload": {"text": text},
        "ui": {},
    }


def _success_response(
    *,
    book_name: str,
    book_id: int,
    record_id: int,
    record_type: str,
    amount: float,
    category_name: str,
    account_name: str,
    payee: str,
    remark: str,
) -> Dict[str, Any]:
    text = (
        f"✅ 已成功记入账本 `{book_name}`！\n"
        f"账本ID：{book_id}｜记录ID：{record_id}\n"
        f"类型：{record_type} {amount:.2f} 元\n"
        f"分类：{category_name}\n"
        f"账户：{account_name or '未填写'}\n"
        f"商家：{payee or '未填写'}\n"
        f"备注：{remark or '无'}"
    )
    return {
        "success": True,
        "text": text,
        "terminal": True,
        "task_outcome": "done",
        "payload": {
            "text": text,
            "book_id": book_id,
            "record_id": record_id,
        },
        "ui": {},
    }


def _resolve_binding_identity(ctx: UnifiedContext) -> tuple[str, str]:
    platform = str(ctx.message.platform or "").strip() or "telegram"
    platform_user_id = str(getattr(ctx.message.user, "id", "") or "").strip()
    user_data = ctx.user_data if isinstance(ctx.user_data, dict) else {}

    if platform == "worker_kernel":
        source_platform = str(
            user_data.get("source_platform")
            or user_data.get("worker_delivery_platform")
            or ""
        ).strip()
        source_user_id = str(
            user_data.get("source_user_id")
            or user_data.get("source_chat_id")
            or user_data.get("worker_delivery_user_id")
            or user_data.get("worker_delivery_chat_id")
            or ""
        ).strip()
        if source_platform:
            platform = source_platform
        if source_user_id:
            platform_user_id = source_user_id

    if not platform_user_id:
        platform_user_id = str(getattr(ctx.message.chat, "id", "") or "").strip()
    return platform, platform_user_id


def _extract_forced_ids(ctx: UnifiedContext) -> tuple[int | None, int | None]:
    user_data = ctx.user_data if isinstance(ctx.user_data, dict) else {}
    forced_user_id = user_data.get("accounting_user_id")
    forced_book_id = user_data.get("accounting_book_id")

    try:
        parsed_user_id = int(forced_user_id) if forced_user_id is not None else None
    except (ValueError, TypeError):
        parsed_user_id = None

    try:
        parsed_book_id = int(forced_book_id) if forced_book_id is not None else None
    except (ValueError, TypeError):
        parsed_book_id = None

    return parsed_user_id, parsed_book_id


def _parse_record_time(record_time_str: str) -> datetime:
    raw = str(record_time_str or "").strip()
    if not raw or raw == "None":
        return datetime.utcnow()

    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    )
    for fmt in candidates:
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.utcnow()


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> Dict[str, Any]:
    """Execute quick accounting from parsed LLM parameters."""
    platform, platform_user_id = _resolve_binding_identity(ctx)
    forced_user_id, forced_book_id = _extract_forced_ids(ctx)

    rtype = params.get("type", "支出")
    try:
        amount = float(params.get("amount", 0.0))
    except (ValueError, TypeError):
        return _failure_response(
            f"金额解析错误，参数：{params.get('amount')}",
            error_code="invalid_amount",
        )

    if amount <= 0:
        return _failure_response(
            "金额必须大于 0",
            error_code="invalid_amount",
        )

    if rtype not in VALID_RECORD_TYPES:
        return _failure_response(
            f"交易类型不支持：{rtype}",
            error_code="invalid_type",
        )

    category_name = str(params.get("category", "未分类")).strip()
    account_name = str(params.get("account", "")).strip()
    target_account_name = str(params.get("target_account", "")).strip()
    payee = str(params.get("payee", "")).strip()
    remark = str(params.get("remark", "")).strip()
    record_time_str = str(params.get("record_time", "")).strip()

    record_time = _parse_record_time(record_time_str)
    if not account_name:
        return _failure_response(
            "账户不能为空，请补充支付账户（如 微信 / 支付宝 / 现金）",
            error_code="missing_account",
        )
    if rtype == "转账" and not target_account_name:
        return _failure_response(
            "转账必须填写 target_account（收款账户）",
            error_code="missing_target_account",
        )

    try:
        session_maker = get_session_maker()
        async with session_maker() as session:
            if forced_user_id is not None:
                user_id = forced_user_id
            else:
                stmt = select(PlatformUserBinding).where(
                    PlatformUserBinding.platform == platform,
                    PlatformUserBinding.platform_user_id == platform_user_id,
                )
                binding = (await session.execute(stmt)).scalars().first()

                if not binding:
                    return _failure_response(
                        (
                            "您未绑定账号，请先在网页端绑定"
                            f"（平台：`{platform}`，ID：`{platform_user_id}`）"
                        ),
                        error_code="binding_not_found",
                    )
                user_id = int(binding.user_id)

            book = None
            if forced_book_id is not None:
                stmt = select(Book).where(
                    Book.id == forced_book_id,
                    Book.owner_id == user_id,
                )
                book = (await session.execute(stmt)).scalars().first()
                if not book:
                    return _failure_response(
                        "指定账本不存在或无权限。",
                        error_code="book_not_found",
                    )
            else:
                active_book_id = await get_active_book_id(user_id)

                stmt = select(Book).where(Book.owner_id == user_id)
                books = list((await session.execute(stmt)).scalars().all())
                if not books:
                    return _failure_response(
                        "您还没有创建账本，请先在网页端创建一个账本。",
                        error_code="book_not_found",
                    )

                if active_book_id is not None:
                    book = next((b for b in books if b.id == active_book_id), None)
                if not book:
                    book = books[0]

            async def get_or_create_account(name: str):
                res = await session.execute(
                    select(Account).where(
                        Account.book_id == book.id, Account.name == name
                    )
                )
                acc = res.scalars().first()
                if not acc:
                    acc = Account(book_id=book.id, name=name)
                    session.add(acc)
                    await session.flush()
                return acc

            async def get_or_create_category(name: str, ctype: str):
                safe_name = name or "未分类"
                res = await session.execute(
                    select(Category).where(
                        Category.book_id == book.id,
                        Category.name == safe_name,
                        Category.type == ctype,
                    )
                )
                cat = res.scalars().first()
                if not cat:
                    cat = Category(book_id=book.id, name=safe_name, type=ctype)
                    session.add(cat)
                    await session.flush()
                return cat

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
            await session.refresh(rec)

            return _success_response(
                book_name=str(book.name),
                book_id=int(book.id),
                record_id=int(rec.id),
                record_type=rtype,
                amount=amount,
                category_name=(category_name or "未分类"),
                account_name=account_name,
                payee=payee,
                remark=remark,
            )
    except SQLAlchemyError as exc:
        logger.exception("quick_accounting database error: %s", exc)
        return _failure_response(
            "数据库写入失败，请稍后重试。",
            error_code="database_error",
        )
    except Exception as exc:
        logger.exception("quick_accounting unexpected error: %s", exc)
        return _failure_response(
            f"记账失败：{exc}",
            error_code="unexpected_error",
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quick accounting skill CLI bridge.",
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--type",
        required=True,
        choices=sorted(VALID_RECORD_TYPES),
        help="Record type",
    )
    parser.add_argument(
        "--amount",
        required=True,
        type=float,
        help="Transaction amount",
    )
    parser.add_argument(
        "--category",
        required=True,
        help="Category name",
    )
    parser.add_argument(
        "--account",
        required=True,
        help="Account name",
    )
    parser.add_argument("--target-account", default="", help="Target account for transfer")
    parser.add_argument("--payee", default="", help="Payee or counterparty")
    parser.add_argument("--remark", default="", help="Optional remark")
    parser.add_argument("--record-time", default="", help="Record time")
    parser.add_argument(
        "--accounting-user-id",
        default="",
        help="Force accounting owner id via ctx.user_data",
    )
    parser.add_argument(
        "--accounting-book-id",
        default="",
        help="Force accounting book id via ctx.user_data",
    )
    return parser


def _build_user_data(args: argparse.Namespace) -> str:
    user_data = parse_json_object(str(args.user_data or "{}"), option_name="--user-data")
    if str(args.accounting_user_id or "").strip():
        user_data["accounting_user_id"] = str(args.accounting_user_id).strip()
    if str(args.accounting_book_id or "").strip():
        user_data["accounting_book_id"] = str(args.accounting_book_id).strip()
    return json.dumps(user_data, ensure_ascii=False)


def _params_from_args(args: argparse.Namespace) -> dict:
    return merge_params(
        args,
        {
            "type": str(args.type or "").strip(),
            "amount": float(args.amount),
            "category": str(args.category or "").strip(),
            "account": str(args.account or "").strip(),
            "target_account": str(args.target_account or "").strip(),
            "payee": str(args.payee or "").strip(),
            "remark": str(args.remark or "").strip(),
            "record_time": str(args.record_time or "").strip(),
        },
    )


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    args.user_data = _build_user_data(args)
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
