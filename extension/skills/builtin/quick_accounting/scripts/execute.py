from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.channel_access import channel_feature_denied_text, is_channel_feature_enabled
from core.platform.models import UnifiedContext
from core.skill_menu import cache_items, get_cached_item, make_callback, parse_callback
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    parse_json_object,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from sqlalchemy import Column, Integer, Table, select
from sqlalchemy.exc import SQLAlchemyError

from api.core.database import Base, get_session_maker


def _ensure_users_table_registered() -> None:
    if "users" in Base.metadata.tables:
        return
    try:
        importlib.import_module("api.auth.models")
    except ModuleNotFoundError as exc:
        missing = str(getattr(exc, "name", "") or "").strip()
        if missing != "fastapi_users":
            raise
    if "users" in Base.metadata.tables:
        return
    Table(
        "users",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        extend_existing=True,
    )


_ensure_users_table_registered()

from api.models.accounting import Account, Book, Category, Record
from api.models.binding import PlatformUserBinding
from core.accounting_store import get_active_book_id, set_active_book_id

logger = logging.getLogger(__name__)
VALID_RECORD_TYPES = {"支出", "收入", "转账"}
ACCOUNTING_MENU_NS = "accu"


def _effective_platform_user_id(ctx: UnifiedContext) -> str:
    callback_user_id = getattr(ctx, "callback_user_id", None)
    if callback_user_id:
        return str(callback_user_id)
    user = getattr(getattr(ctx, "message", None), "user", None)
    if user is not None and getattr(user, "id", None) is not None:
        return str(user.id)
    return str(getattr(getattr(getattr(ctx, "message", None), "chat", None), "id", "") or "").strip()


def _accounting_enabled(ctx: UnifiedContext) -> bool:
    return is_channel_feature_enabled(
        platform=str(getattr(getattr(ctx, "message", None), "platform", "") or "").strip().lower(),
        platform_user_id=_effective_platform_user_id(ctx),
        feature="accounting",
    )


def _parse_accounting_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "help", ""
    parts = raw.split(maxsplit=2)
    if not parts:
        return "help", ""
    if not parts[0].startswith("/acc"):
        return "help", ""
    if len(parts) == 1:
        return "info", ""
    return str(parts[1] or "").strip().lower(), str(parts[2] if len(parts) >= 3 else "").strip()


def _accounting_usage_text() -> str:
    return (
        "📊 记账助手用法:\n\n"
        "`/acc info` - 查看当前记账账本和简要统计\n"
        "`/acc list` - 列出你名下的所有账本\n"
        "`/acc use <账本ID/名称>` - 切换默认记账账本\n"
        "`/acc record <文字/发图>` - 快捷记账支持\n"
        "`/acc help` - 帮助\n\n"
        "💡 Tip: 也可以直接发送带有消费信息的图片或文字，Bot会自动帮你记账。"
    )


def _accounting_menu_ui() -> dict:
    return {
        "actions": [
            [
                {"text": "📈 当前账本", "callback_data": make_callback(ACCOUNTING_MENU_NS, "info")},
                {"text": "📚 账本列表", "callback_data": make_callback(ACCOUNTING_MENU_NS, "list")},
            ],
            [
                {"text": "🧾 记账说明", "callback_data": make_callback(ACCOUNTING_MENU_NS, "record")},
                {"text": "ℹ️ 帮助", "callback_data": make_callback(ACCOUNTING_MENU_NS, "help")},
            ],
        ]
    }


async def _get_user_id_from_binding(platform: str, platform_user_id: str) -> int | None:
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = select(PlatformUserBinding).where(
            PlatformUserBinding.platform == platform,
            PlatformUserBinding.platform_user_id == platform_user_id,
        )
        binding = (await session.execute(stmt)).scalars().first()
        if binding:
            return int(binding.user_id)
        return None


async def _list_books_for_user(user_id: int) -> list[Book]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = select(Book).where(Book.owner_id == user_id)
        return list((await session.execute(stmt)).scalars().all())


async def _resolve_bound_user_id(ctx: UnifiedContext) -> tuple[int | None, str, str]:
    platform = str(getattr(getattr(ctx, "message", None), "platform", "") or "telegram").strip() or "telegram"
    platform_user_id = _effective_platform_user_id(ctx)
    user_id = await _get_user_id_from_binding(platform, platform_user_id)
    return user_id, platform, platform_user_id


async def build_accounting_info_payload(
    ctx: UnifiedContext,
    *,
    prefix: str = "",
) -> tuple[str, dict]:
    user_id, platform, platform_user_id = await _resolve_bound_user_id(ctx)
    if not user_id:
        return (
            f"❌ 您还未绑定网页端账号。请先绑定。您的 ID：`{platform_user_id}`, 平台：`{platform}`",
            _accounting_menu_ui(),
        )

    books = await _list_books_for_user(user_id)
    if not books:
        return ("❌ 您还未创建任何账本，请先在系统内创建一个账本。", _accounting_menu_ui())

    active_book_id = await get_active_book_id(user_id)
    book = next((b for b in books if b.id == active_book_id), None)
    if not book:
        book = books[0]
        await set_active_book_id(user_id, book.id)

    cache_items(
        ctx,
        ACCOUNTING_MENU_NS,
        "books",
        [{"id": int(item.id), "name": str(item.name)} for item in books],
    )

    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.extend(
        [
            "📈 记账助手",
            "",
            f"当前账本：**{book.name}**",
            f"账本 ID：`{book.id}`",
            f"账本总数：`{len(books)}`",
            "",
            "也支持直接发消费文字或截图，Bot 会自动尝试记账。",
        ]
    )
    return "\n".join(lines), _accounting_menu_ui()


async def _build_accounting_list_payload(
    ctx: UnifiedContext,
    *,
    prefix: str = "",
) -> tuple[str, dict]:
    user_id, platform, platform_user_id = await _resolve_bound_user_id(ctx)
    if not user_id:
        return (
            f"❌ 您还未绑定网页端账号。请先绑定。您的 ID：`{platform_user_id}`, 平台：`{platform}`",
            _accounting_menu_ui(),
        )

    books = await _list_books_for_user(user_id)
    if not books:
        return ("❌ 您还未创建任何账本，请先在系统内创建一个账本。", _accounting_menu_ui())

    active_book_id = await get_active_book_id(user_id)
    if active_book_id is None:
        active_book_id = books[0].id
        await set_active_book_id(user_id, books[0].id)

    payload_books = [{"id": int(item.id), "name": str(item.name)} for item in books]
    cache_items(ctx, ACCOUNTING_MENU_NS, "books", payload_books)

    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.append("📚 您的账本列表：")
    for item in payload_books:
        marker = "👉" if int(item["id"]) == int(active_book_id) else "  "
        lines.append(f"{marker} `{item['id']}` | **{item['name']}**")
    lines.append("")
    lines.append("点击按钮可直接切换账本。")

    actions: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for index, item in enumerate(payload_books):
        prefix_text = "✅ " if int(item["id"]) == int(active_book_id) else ""
        row.append(
            {
                "text": f"{prefix_text}{str(item['name'])[:14]}",
                "callback_data": make_callback(ACCOUNTING_MENU_NS, "use", index),
            }
        )
        if len(row) == 2:
            actions.append(row)
            row = []
    if row:
        actions.append(row)
    actions.append(
        [
            {"text": "📈 当前账本", "callback_data": make_callback(ACCOUNTING_MENU_NS, "info")},
            {"text": "ℹ️ 帮助", "callback_data": make_callback(ACCOUNTING_MENU_NS, "help")},
        ]
    )
    return "\n".join(lines), {"actions": actions}


async def _switch_book_payload(
    ctx: UnifiedContext,
    target: str,
) -> tuple[str, dict]:
    user_id, platform, platform_user_id = await _resolve_bound_user_id(ctx)
    if not user_id:
        return (
            f"❌ 您还未绑定网页端账号。请先绑定。您的 ID：`{platform_user_id}`, 平台：`{platform}`",
            _accounting_menu_ui(),
        )

    books = await _list_books_for_user(user_id)
    if not books:
        return ("❌ 您还未创建任何账本，请先在系统内创建一个账本。", _accounting_menu_ui())

    found_book = None
    if target.isdigit():
        found_book = next((b for b in books if b.id == int(target)), None)
    if not found_book:
        found_book = next((b for b in books if b.name == target), None)
    if not found_book:
        return (f"❌ 找不到名为或 ID 为 `{target}` 的账本。", _accounting_menu_ui())

    await set_active_book_id(user_id, found_book.id)
    return await _build_accounting_list_payload(
        ctx,
        prefix=f"✅ 当前记账账本已切换为：**{found_book.name}**",
    )


def _record_help_text() -> str:
    return (
        "🧾 **快捷记账说明**\n\n"
        "直接发送消费描述或票据截图即可，例如：\n"
        "• 午饭 32 元\n"
        "• 打车 18.5\n"
        "• 发送小票照片\n\n"
        "也可以用 `/acc record <文字>` 查看说明。"
    )


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

    if platform == "subagent_kernel":
        source_platform = str(
            user_data.get("source_platform")
            or user_data.get("subagent_delivery_platform")
            or ""
        ).strip()
        source_user_id = str(
            user_data.get("source_user_id")
            or user_data.get("source_chat_id")
            or user_data.get("subagent_delivery_user_id")
            or user_data.get("subagent_delivery_chat_id")
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
    denied_text = channel_feature_denied_text("accounting")
    if not is_channel_feature_enabled(
        platform=str(ctx.message.platform or "").strip().lower(),
        platform_user_id=str(getattr(ctx.message.user, "id", "") or "").strip(),
        feature="accounting",
    ):
        return {
            "success": False,
            "error_code": "feature_disabled",
            "text": denied_text,
            "terminal": True,
            "payload": {"text": denied_text},
            "ui": {},
        }
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


def register_handlers(adapter_manager):
    from core.config import is_user_allowed

    async def cmd_acc(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        if not _accounting_enabled(ctx):
            return {"text": channel_feature_denied_text("accounting"), "ui": {}}

        sub, args = _parse_accounting_subcommand(ctx.message.text or "")
        if sub in {"help", "h", "?"}:
            return {"text": _accounting_usage_text(), "ui": _accounting_menu_ui()}
        if sub in {"info", "i"}:
            payload, ui = await build_accounting_info_payload(ctx)
            return {"text": payload, "ui": ui}
        if sub in {"list", "ls"}:
            payload, ui = await _build_accounting_list_payload(ctx)
            return {"text": payload, "ui": ui}
        if sub == "use":
            target = args.strip()
            if not target:
                return {"text": "用法: `/acc use <账本ID或名称>`", "ui": _accounting_menu_ui()}
            payload, ui = await _switch_book_payload(ctx, target)
            return {"text": payload, "ui": ui}
        if sub == "record":
            if not args:
                return {"text": "直接在后面输入信息即可，或发送带有收支金额的截图。", "ui": _accounting_menu_ui()}
            return {
                "text": (
                    "提示: 此指令可以配合大模型智能截取参数。对于强制单步记录，请使用普通的语言描述。"
                    "您甚至无需加 `/acc record`。"
                ),
                "ui": _accounting_menu_ui(),
            }
        return {"text": _accounting_usage_text(), "ui": _accounting_menu_ui()}

    async def handle_accounting_callback(ctx: UnifiedContext) -> None:
        if not _accounting_enabled(ctx):
            await ctx.reply(channel_feature_denied_text("accounting"))
            return

        data = ctx.callback_data
        if not data:
            return

        action, parts = parse_callback(data, ACCOUNTING_MENU_NS)
        if not action:
            return

        await ctx.answer_callback()

        if action == "info":
            payload, ui = await build_accounting_info_payload(ctx)
        elif action == "list":
            payload, ui = await _build_accounting_list_payload(ctx)
        elif action == "record":
            payload = _record_help_text()
            ui = _accounting_menu_ui()
        elif action == "help":
            payload = _accounting_usage_text()
            ui = _accounting_menu_ui()
        elif action == "use":
            cached = get_cached_item(
                ctx,
                ACCOUNTING_MENU_NS,
                "books",
                parts[0] if parts else "",
            )
            if not cached:
                payload, ui = await _build_accounting_list_payload(
                    ctx,
                    prefix="❌ 账本列表已过期，请重新选择。",
                )
            else:
                payload, ui = await _switch_book_payload(
                    ctx,
                    str(cached.get("id") or ""),
                )
        else:
            payload = _accounting_usage_text()
            ui = _accounting_menu_ui()

        await ctx.edit_message(ctx.message.id, payload, ui=ui)

    adapter_manager.on_command("acc", cmd_acc, description="快捷记账助手")
    adapter_manager.on_callback_query("^accu_", handle_accounting_callback)


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


from core.extension_base import SkillExtension


class QuickAccountingSkillExtension(SkillExtension):
    name = "quick_accounting_extension"
    skill_name = "quick_accounting"

    def register(self, runtime) -> None:
        register_handlers(runtime.adapter_manager)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
