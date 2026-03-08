"""
Stock Watch Skill Script
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from core.state_store import (
    remove_watchlist_stock,
    get_user_watchlist,
    add_watchlist_stock,
)
from core.platform.models import UnifiedContext
import logging

if __package__:
    from .services.stock_service import (
        fetch_stock_quotes,
        format_stock_message,
        search_stock_by_name,
    )
else:
    from services.stock_service import (
        fetch_stock_quotes,
        format_stock_message,
        search_stock_by_name,
    )

logger = logging.getLogger(__name__)


def _parse_stock_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "list", ""

    parts = raw.split(maxsplit=2)
    if not parts:
        return "list", ""
    if not parts[0].startswith("/stock"):
        return "help", ""
    if len(parts) == 1:
        return "list", ""

    sub = str(parts[1] or "").strip().lower()
    args = str(parts[2] if len(parts) >= 3 else "").strip()

    if sub in {"list", "ls", "show"}:
        return "list", ""
    if sub in {"add", "add_stock"}:
        return "add", args
    if sub in {"remove", "rm", "del", "delete", "remove_stock"}:
        return "remove", args
    if sub in {"refresh", "run", "check"}:
        return "refresh", ""
    if sub in {"help", "h", "?"}:
        return "help", ""
    return "help", ""


def _stock_usage_text() -> str:
    return (
        "用法:\n"
        "`/stock list`\n"
        "`/stock add <股票名称或代码>`\n"
        "`/stock remove <股票名称或代码>`\n"
        "`/stock refresh`\n"
        "`/stock help`"
    )


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> str:
    """执行自选股操作"""
    from core.scheduler import trigger_manual_stock_check

    user_id = ctx.message.user.id
    raw_action = params.get("action", "list")
    stock_name = params.get("stock_name", "")

    # 兼容性映射：防止 AI 输出中文 Action
    ACTION_MAP = {
        "添加": "add_stock",
        "关注": "add_stock",
        "add": "add_stock",
        "删除": "remove_stock",
        "取消": "remove_stock",
        "取消关注": "remove_stock",
        "remove": "remove_stock",
        "delete": "remove_stock",
        "查看": "list",
        "列表": "list",
        "list": "list",
        "刷新": "refresh",
        "更新": "refresh",
        "refresh": "refresh",
    }
    action = ACTION_MAP.get(raw_action, raw_action)

    if action == "refresh":
        result = await trigger_manual_stock_check(user_id)
        if result:
            return {
                "text": f"✅ 股票行情已刷新。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result}",
                "ui": {},
            }
        else:
            return {"text": "📭 您的自选股列表为空，无法刷新。", "ui": {}}

    if action == "add_stock":
        if "," in stock_name or " " in stock_name or "，" in stock_name:
            names = [n.strip() for n in re.split(r"[,，\s]+", stock_name) if n.strip()]
            return await add_multiple_stocks(ctx, user_id, names)
        else:
            return await add_single_stock(ctx, user_id, stock_name)

    if action == "remove_stock":
        return await remove_stock(ctx, user_id, stock_name)

    if action == "list" or not stock_name:
        return await show_watchlist(ctx, user_id)


def register_handlers(adapter_manager):
    """注册 Stock 二级命令和 Callback"""
    from core.config import is_user_allowed

    async def cmd_stock(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        sub, args = _parse_stock_subcommand(ctx.message.text or "")
        user_id = ctx.message.user.id

        if sub == "list":
            return await show_watchlist(ctx, user_id)

        if sub == "add":
            name = args.strip()
            if not name:
                return {"text": "用法: `/stock add <股票名称或代码>`", "ui": {}}
            if "," in name or " " in name or "，" in name:
                names = [n.strip() for n in re.split(r"[,，\s]+", name) if n.strip()]
                return await add_multiple_stocks(ctx, user_id, names)
            return await add_single_stock(ctx, user_id, name)

        if sub == "remove":
            name = args.strip()
            if not name:
                return {"text": "用法: `/stock remove <股票名称或代码>`", "ui": {}}
            return await remove_stock(ctx, user_id, name)

        if sub == "refresh":
            from core.scheduler import trigger_manual_stock_check

            result = await trigger_manual_stock_check(user_id)
            if result:
                return {
                    "text": f"✅ 股票行情已刷新。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result}",
                    "ui": {},
                }
            return {"text": "📭 您的自选股列表为空，无法刷新。", "ui": {}}

        return {"text": _stock_usage_text(), "ui": {}}

    adapter_manager.on_command("stock", cmd_stock, description="自选股管理")

    # Callback
    adapter_manager.on_callback_query("^stock_", handle_stock_select_callback)

    # "del_stock_" is handled by generic handle_subscription_callback in old code?
    # No, stock_del_ is in handle_stock_select_callback now (refactored previously).
    # Wait, previous refactor moved handle_stock_select_callback to handlers/stock_handlers.py
    # and it handles stock_del_.
    # Check handle_stock_select_callback below.


async def show_watchlist(ctx: UnifiedContext, user_id: int) -> str:
    """显示自选股列表"""
    # Note: caller should handle permission if needed
    platform = ctx.message.platform if ctx.message.platform else "telegram"
    watchlist = await get_user_watchlist(user_id, platform=platform)

    if not watchlist:
        return {
            "text": "📭 **您的自选股为空**\n\n发送「帮我关注 XX股票」可添加自选股。",
            "ui": {},
        }

    stock_codes = [item["stock_code"] for item in watchlist]
    quotes = await fetch_stock_quotes(stock_codes)

    if quotes:
        message = format_stock_message(quotes)
    else:
        lines = ["📈 **我的自选股**\n"]
        for item in watchlist:
            lines.append(f"• {item['stock_name']} ({item['stock_code']})")
        message = "\n".join(lines)

    actions = []
    temp_row = []
    for item in watchlist:
        btn = {
            "text": f"❌ {item['stock_name']}",
            "callback_data": f"stock_del_{item['stock_code']}",
        }
        temp_row.append(btn)

        if len(temp_row) == 2:
            actions.append(temp_row)
            temp_row = []

    if temp_row:
        actions.append(temp_row)

    return {"text": message, "ui": {"actions": actions}}


async def remove_stock(ctx: UnifiedContext, user_id: int, stock_name: str) -> str:
    """删除自选股"""
    platform = ctx.message.platform if ctx.message.platform else "telegram"
    watchlist = await get_user_watchlist(user_id, platform=platform)
    for item in watchlist:
        if stock_name.lower() in item["stock_name"].lower():
            await remove_watchlist_stock(user_id, item["stock_code"])
            return {"text": f"✅ 已取消关注 **{item['stock_name']}**", "ui": {}}
    return {"text": f"⚠️ 未找到匹配「{stock_name}」的自选股", "ui": {}}


async def add_multiple_stocks(
    ctx: UnifiedContext, user_id: int, stock_names: list[str]
) -> str:
    """添加多个股票"""

    success_list = []
    failed_list = []
    existed_list = []

    platform = ctx.message.platform if ctx.message.platform else "telegram"

    for name in stock_names:
        results = await search_stock_by_name(name)

        if not results:
            failed_list.append(name)
        elif len(results) == 1:
            stock = results[0]
            success = await add_watchlist_stock(
                user_id, stock["code"], stock["name"], platform=platform
            )
            if success:
                success_list.append(stock["name"])
            else:
                existed_list.append(stock["name"])
        else:
            stock = results[0]
            success = await add_watchlist_stock(
                user_id, stock["code"], stock["name"], platform=platform
            )
            if success:
                success_list.append(f"{stock['name']}(自动匹配)")
            else:
                existed_list.append(stock["name"])

    result_parts = []
    if success_list:
        result_parts.append(f"✅ 已添加：{', '.join(success_list)}")
    if existed_list:
        result_parts.append(f"⚠️ 已存在：{', '.join(existed_list)}")
    if failed_list:
        result_parts.append(f"❌ 未找到：{', '.join(failed_list)}")

    result_msg = (
        "**自选股添加完成！**\n\n"
        + "\n".join(result_parts)
        + "\n\n交易时段将每 10 分钟推送行情。"
    )

    # await ctx.edit_message(
    #    getattr(msg, "message_id", getattr(msg, "id", None)), result_msg
    # )
    # For Agent flow, we return result. For native (if used), we rely on return.
    # Note: add_multiple_stocks is called by cmd_add_stock too.
    # We should return the dict.

    return {"text": result_msg, "ui": {}}


async def add_single_stock(ctx: UnifiedContext, user_id: int, stock_name: str) -> str:
    """添加单个股票"""

    results = await search_stock_by_name(stock_name)
    platform = ctx.message.platform if ctx.message.platform else "telegram"
    logger.info(f"Adding single stock for user {user_id} on platform: {platform}")

    if not results:
        msg_text = f"❌ 未找到匹配「{stock_name}」的股票"
        return {"text": msg_text, "ui": {}}

    if len(results) == 1:
        stock = results[0]
        success = await add_watchlist_stock(
            user_id, stock["code"], stock["name"], platform=platform
        )
        if success:
            msg_text = (
                f"✅ 已添加自选股 ({platform})\n\n"
                f"**{stock['name']}** ({stock['code']})\n\n"
                f"交易时段将每 10 分钟推送行情。"
            )
            # await ctx.edit_message(
            #    getattr(msg, "message_id", getattr(msg, "id", None)),
            #    msg_text,
            # )
            return {"text": msg_text, "ui": {}}
        else:
            msg_text = f"⚠️ **{stock['name']}** 已在您的自选股中"
            return {"text": msg_text, "ui": {}}

    actions = []
    for stock in results[:8]:
        actions.append(
            [
                {
                    "text": f"{stock['name']} ({stock['code']}) - {stock['market']}",
                    "callback_data": f"stock_add_{stock['code']}_{stock['name']}",
                }
            ]
        )
    actions.append([{"text": "🚫 取消", "callback_data": "stock_cancel"}])

    msg_text = f"🔍 找到多个匹配「{stock_name}」的股票，请选择："

    return {"text": msg_text, "ui": {"actions": actions}}


async def handle_stock_select_callback(ctx: UnifiedContext) -> None:
    """处理用户点击选择股票的回调"""
    data = ctx.callback_data
    if not data:
        return

    await ctx.answer_callback()

    user_id = ctx.callback_user_id
    platform = ctx.message.platform if ctx.message.platform else "telegram"

    if data == "stock_cancel":
        await ctx.edit_message(ctx.message.id, "👌 已取消操作。")
        return

    if data.startswith("stock_add_"):
        parts = data.replace("stock_add_", "").split("_", 1)
        if len(parts) == 2:
            stock_code, stock_name = parts
            success = await add_watchlist_stock(
                user_id, stock_code, stock_name, platform=platform
            )
            if success:
                await ctx.edit_message(
                    ctx.message.id,
                    f"✅ 已添加自选股\n\n"
                    f"**{stock_name}** ({stock_code})\n\n"
                    f"交易时段将每 10 分钟推送行情。",
                )
            else:
                await ctx.edit_message(
                    ctx.message.id, f"⚠️ **{stock_name}** 已在您的自选股中"
                )
        return

    if data.startswith("stock_del_"):
        stock_code = data.replace("stock_del_", "")
        success = await remove_watchlist_stock(user_id, stock_code)
        if success:
            await ctx.edit_message(ctx.message.id, f"✅ 已取消关注 {stock_code}")
        else:
            await ctx.edit_message(ctx.message.id, "❌ 删除失败")
        return


def _resolve_cli_user_id(explicit_user_id: str | None) -> str:
    raw = str(explicit_user_id or os.getenv("X_BOT_RUNTIME_USER_ID") or "").strip()
    if raw.startswith("worker::"):
        parts = raw.split("::")
        if len(parts) >= 3:
            candidate = str(parts[2] or "").strip()
            if candidate:
                return candidate
    return raw


def _resolve_cli_platform(explicit_platform: str | None) -> str:
    platform = str(
        explicit_platform or os.getenv("X_BOT_RUNTIME_PLATFORM") or ""
    ).strip().lower()
    if not platform or platform == "worker_kernel":
        return "telegram"
    return platform


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage watchlist and quotes with the built-in stock service.",
    )
    parser.add_argument(
        "--user-id",
        default="",
        help="Optional runtime user id. Defaults to X_BOT_RUNTIME_USER_ID.",
    )
    parser.add_argument(
        "--platform",
        default="",
        help="Optional platform name. Defaults to X_BOT_RUNTIME_PLATFORM or telegram.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List watchlist with latest quotes")
    subparsers.add_parser("refresh", help="Alias of list")

    quotes_parser = subparsers.add_parser(
        "quotes",
        help="Fetch quotes for explicit stock codes or current watchlist",
    )
    quotes_parser.add_argument("symbols", nargs="*")

    search_parser = subparsers.add_parser("search", help="Search stock by name/code")
    search_parser.add_argument("keyword")

    add_parser = subparsers.add_parser("add", help="Add a stock to watchlist")
    add_parser.add_argument("keyword")

    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove a stock from watchlist by code or fuzzy name",
    )
    remove_parser.add_argument("keyword")

    return parser


async def _cli_print_watchlist(user_id: str, platform: str) -> int:
    watchlist = await get_user_watchlist(user_id, platform=platform)
    if not watchlist:
        print("watchlist is empty")
        return 0

    quotes = await fetch_stock_quotes(
        [str(item.get("stock_code") or "").strip() for item in watchlist]
    )
    if quotes:
        print(format_stock_message(quotes))
    else:
        for item in watchlist:
            print(
                f"{item.get('stock_name', '')}\t{item.get('stock_code', '')}\tplatform={platform}"
            )
    return 0


async def _cli_print_quotes(args: argparse.Namespace, user_id: str, platform: str) -> int:
    symbols = [str(item or "").strip() for item in list(args.symbols or []) if str(item or "").strip()]
    if not symbols:
        watchlist = await get_user_watchlist(user_id, platform=platform)
        symbols = [
            str(item.get("stock_code") or "").strip()
            for item in watchlist
            if str(item.get("stock_code") or "").strip()
        ]
    if not symbols:
        print("watchlist is empty")
        return 0

    quotes = await fetch_stock_quotes(symbols)
    if not quotes:
        print("no quotes found")
        return 1
    print(format_stock_message(quotes))
    return 0


async def _cli_search(keyword: str) -> int:
    results = await search_stock_by_name(keyword)
    if not results:
        print(f"no stock found for: {keyword}")
        return 1
    for item in results:
        print(
            f"{item.get('name', '')}\t{item.get('code', '')}\tmarket={item.get('market', '')}"
        )
    return 0


def _select_single_stock(results: list[dict], keyword: str) -> dict | None:
    normalized = str(keyword or "").strip().lower()
    exact = [
        item
        for item in results
        if normalized
        and normalized
        in {
            str(item.get("code") or "").strip().lower(),
            str(item.get("name") or "").strip().lower(),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if len(results) == 1:
        return results[0]
    return None


async def _cli_add(keyword: str, user_id: str, platform: str) -> int:
    results = await search_stock_by_name(keyword)
    if not results:
        print(f"no stock found for: {keyword}")
        return 1

    selected = _select_single_stock(results, keyword)
    if not selected:
        print("multiple stocks matched, please refine your query:")
        for item in results[:8]:
            print(
                f"- {item.get('name', '')}\t{item.get('code', '')}\tmarket={item.get('market', '')}"
            )
        return 2

    stock_code = str(selected.get("code") or "").strip()
    stock_name = str(selected.get("name") or stock_code).strip()
    success = await add_watchlist_stock(
        user_id,
        stock_code,
        stock_name,
        platform=platform,
    )
    if success:
        print(f"added\t{stock_name}\t{stock_code}\tplatform={platform}")
        return 0
    print(f"already_exists\t{stock_name}\t{stock_code}")
    return 0


async def _cli_remove(keyword: str, user_id: str, platform: str) -> int:
    watchlist = await get_user_watchlist(user_id, platform=platform)
    normalized = str(keyword or "").strip().lower()
    if not normalized:
        print("keyword is required", file=sys.stderr)
        return 1

    exact = [
        item
        for item in watchlist
        if normalized
        in {
            str(item.get("stock_code") or "").strip().lower(),
            str(item.get("stock_name") or "").strip().lower(),
        }
    ]
    if len(exact) == 1:
        target = exact[0]
    else:
        fuzzy = [
            item
            for item in watchlist
            if normalized in str(item.get("stock_name") or "").strip().lower()
        ]
        if len(fuzzy) == 1:
            target = fuzzy[0]
        elif len(fuzzy) > 1:
            print("multiple watchlist entries matched, please refine your query:")
            for item in fuzzy:
                print(
                    f"- {item.get('stock_name', '')}\t{item.get('stock_code', '')}"
                )
            return 2
        else:
            print(f"watchlist stock not found: {keyword}")
            return 1

    stock_code = str(target.get("stock_code") or "").strip()
    stock_name = str(target.get("stock_name") or stock_code).strip()
    success = await remove_watchlist_stock(user_id, stock_code)
    if not success:
        print(f"failed_to_remove\t{stock_name}\t{stock_code}", file=sys.stderr)
        return 1
    print(f"removed\t{stock_name}\t{stock_code}")
    return 0


async def _run_cli() -> int:
    parser = _build_cli_parser()
    args = parser.parse_args()
    user_id = _resolve_cli_user_id(args.user_id)
    if not user_id:
        print(
            "missing runtime user id: set X_BOT_RUNTIME_USER_ID or pass --user-id",
            file=sys.stderr,
        )
        return 1

    platform = _resolve_cli_platform(args.platform)
    command = str(args.command or "").strip().lower()
    if command in {"list", "refresh"}:
        return await _cli_print_watchlist(user_id, platform)
    if command == "quotes":
        return await _cli_print_quotes(args, user_id, platform)
    if command == "search":
        return await _cli_search(str(args.keyword or "").strip())
    if command == "add":
        return await _cli_add(str(args.keyword or "").strip(), user_id, platform)
    if command == "remove":
        return await _cli_remove(str(args.keyword or "").strip(), user_id, platform)
    print(f"unsupported command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_cli()))
