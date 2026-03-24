"""
Stock Watch Skill Script
"""

import argparse
import asyncio
import datetime
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from core.channel_access import channel_feature_denied_text, is_channel_feature_enabled
from core.state_store import (
    remove_watchlist_stock,
    get_user_watchlist,
    get_all_watchlist_users,
    add_watchlist_stock,
    get_feature_delivery_target,
    set_feature_delivery_target,
)
from core.skill_menu import make_callback, parse_callback
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
STOCK_MENU_NS = "stkm"
STOCK_PUSH_INTERVAL_SEC = 10 * 60


def _stock_enabled(ctx: UnifiedContext) -> bool:
    return is_channel_feature_enabled(
        platform=str(ctx.message.platform or "").strip().lower(),
        platform_user_id=str(ctx.message.user.id or "").strip(),
        feature="stock",
    )


def _format_delivery_target(target: dict[str, str] | None) -> str:
    platform = str((target or {}).get("platform") or "").strip()
    chat_id = str((target or {}).get("chat_id") or "").strip()
    if not platform or not chat_id:
        return "未设置"
    return f"{platform}:{chat_id}"


def _current_delivery_target(ctx: UnifiedContext) -> dict[str, str]:
    return {
        "platform": str(ctx.message.platform or "telegram").strip() or "telegram",
        "chat_id": str(getattr(ctx.message.chat, "id", "") or "").strip(),
    }


async def _ensure_default_stock_delivery_target(
    ctx: UnifiedContext,
    user_id: int | str,
) -> dict[str, str]:
    current = await get_feature_delivery_target(user_id, "stock")
    if current:
        return current
    target = _current_delivery_target(ctx)
    if target["platform"] and target["chat_id"]:
        return await set_feature_delivery_target(
            user_id,
            "stock",
            target["platform"],
            target["chat_id"],
        )
    return {}


def _parse_stock_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "menu", ""

    parts = raw.split(maxsplit=2)
    if not parts:
        return "list", ""
    if not parts[0].startswith("/stock"):
        return "help", ""
    if len(parts) == 1:
        return "menu", ""

    sub = str(parts[1] or "").strip().lower()
    args = str(parts[2] if len(parts) >= 3 else "").strip()

    if sub in {"menu", "home", "start"}:
        return "menu", ""
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
        "`/stock`\n"
        "`/stock list`\n"
        "`/stock add <股票名称或代码>`\n"
        "`/stock remove <股票名称或代码>`\n"
        "`/stock refresh`\n"
        "`/stock help`"
    )


def _stock_home_ui() -> dict:
    return {
        "actions": [
            [
                {"text": "📋 我的自选股", "callback_data": make_callback(STOCK_MENU_NS, "list")},
                {"text": "🔄 刷新行情", "callback_data": make_callback(STOCK_MENU_NS, "refresh")},
            ],
            [
                {"text": "📍 设为当前渠道", "callback_data": make_callback(STOCK_MENU_NS, "bind")},
                {"text": "ℹ️ 帮助", "callback_data": make_callback(STOCK_MENU_NS, "help")},
            ],
            [
                {"text": "➕ 如何添加", "callback_data": make_callback(STOCK_MENU_NS, "addhelp")},
            ],
        ]
    }


async def show_stock_menu(ctx: UnifiedContext, user_id: int | str) -> dict:
    watchlist = await get_user_watchlist(user_id)
    delivery_target = await get_feature_delivery_target(user_id, "stock")
    names = [str(item.get("stock_name") or "").strip() for item in watchlist[:4] if str(item.get("stock_name") or "").strip()]
    summary = "、".join(names)
    if len(watchlist) > 4:
        summary += " 等"
    if not summary:
        summary = "暂无自选股"

    return {
        "text": (
            "📈 **自选股管理**\n\n"
            f"当前数量：{len(watchlist)}\n"
            f"当前列表：{summary}\n\n"
            f"推送渠道：`{_format_delivery_target(delivery_target)}`\n\n"
            "支持直接输入：`/stock add <名称或代码>`、`/stock remove <名称或代码>`。"
        ),
        "ui": _stock_home_ui(),
    }


def _stock_add_help_response() -> dict:
    return {
        "text": (
            "➕ **添加自选股**\n\n"
            "直接发送以下命令：\n"
            "• `/stock add 贵州茅台`\n"
            "• `/stock add sh600519`\n"
            "• `/stock add 茅台 腾讯 苹果`\n\n"
            "如果命中多个股票，我会给你按钮继续点选。"
        ),
        "ui": {
            "actions": [
                [
                    {"text": "🏠 返回首页", "callback_data": make_callback(STOCK_MENU_NS, "home")},
                    {"text": "📋 查看自选股", "callback_data": make_callback(STOCK_MENU_NS, "list")},
                ]
            ]
        },
    }


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> str:
    """执行自选股操作"""
    if not _stock_enabled(ctx):
        return {"text": channel_feature_denied_text("stock"), "ui": {}}

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


def is_trading_time(now: datetime.datetime | None = None) -> bool:
    """判断当前是否为 A 股交易时段。"""
    current = now or datetime.datetime.now()
    if current.weekday() >= 5:
        return False

    current_time = current.time()
    return (
        datetime.time(9, 30) <= current_time <= datetime.time(11, 30)
        or datetime.time(13, 0) <= current_time <= datetime.time(15, 0)
    )


async def stock_push_job() -> None:
    """交易时段定时推送自选股行情。"""
    if not is_trading_time():
        logger.debug("Not trading time, skipping stock push")
        return

    logger.info("Starting stock push job...")

    from core.scheduler import (
        _remember_proactive_delivery_target,
        _resolve_proactive_delivery_target,
        send_via_adapter,
    )

    try:
        users_with_platform = await get_all_watchlist_users()
        if not users_with_platform:
            logger.info("No users with watchlist, skipping")
            return

        for user_id, platform in users_with_platform:
            try:
                watchlist = await get_user_watchlist(user_id)
                if not watchlist:
                    continue

                stock_codes = [item["stock_code"] for item in watchlist]
                quotes = await fetch_stock_quotes(stock_codes)
                if not quotes:
                    continue

                message = format_stock_message(quotes)
                stock_delivery_target = await get_feature_delivery_target(
                    user_id, "stock"
                )
                target_platform, target_chat_id = await _resolve_proactive_delivery_target(
                    user_id,
                    platform,
                    metadata=(
                        {
                            "resource_binding": {
                                "platform": str(
                                    stock_delivery_target.get("platform") or platform
                                ),
                                "chat_id": str(
                                    stock_delivery_target.get("chat_id") or ""
                                ).strip(),
                            }
                        }
                        if str(stock_delivery_target.get("chat_id") or "").strip()
                        else None
                    ),
                )
                if not target_platform or not target_chat_id:
                    logger.warning(
                        "Stock push skipped: no delivery target for user=%s on %s",
                        user_id,
                        platform,
                    )
                    continue

                await send_via_adapter(
                    chat_id=target_chat_id,
                    text=message,
                    platform=target_platform,
                    user_id=user_id,
                    record_history=True,
                )
                await _remember_proactive_delivery_target(
                    user_id,
                    target_platform,
                    target_chat_id,
                )
                logger.info(
                    "Sent stock quotes to user %s on %s",
                    user_id,
                    target_platform,
                )
            except Exception as exc:
                logger.error(
                    "Failed to send stock quotes to %s on %s: %s",
                    user_id,
                    platform,
                    exc,
                )
    except Exception as exc:
        logger.error("Stock push job error: %s", exc)


async def trigger_manual_stock_check(user_id: int | str) -> str:
    """手动刷新指定用户的自选股行情。"""
    try:
        watchlist = await get_user_watchlist(user_id)
        if not watchlist:
            return ""

        stock_codes = [item["stock_code"] for item in watchlist]
        quotes = await fetch_stock_quotes(stock_codes)
        if not quotes:
            return "❌ 无法获取行情数据，请稍后重试。"
        return format_stock_message(quotes)
    except Exception as exc:
        logger.error("Manual stock check error for %s: %s", user_id, exc)
        return f"❌ 刷新失败: {str(exc)}"


def register_jobs(scheduler) -> None:
    scheduler.add_job(
        stock_push_job,
        "interval",
        seconds=STOCK_PUSH_INTERVAL_SEC,
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=5),
        id="skill_stock_watch_push",
        replace_existing=True,
    )
    logger.info(
        "Registered stock_watch scheduled job: interval=%ss",
        STOCK_PUSH_INTERVAL_SEC,
    )


def register_handlers(adapter_manager):
    """注册 Stock 二级命令和 Callback"""
    from core.config import is_user_allowed

    async def cmd_stock(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        if not _stock_enabled(ctx):
            return {"text": channel_feature_denied_text("stock"), "ui": {}}
        sub, args = _parse_stock_subcommand(ctx.message.text or "")
        user_id = ctx.message.user.id

        if sub == "menu":
            return await show_stock_menu(ctx, user_id)

        if sub == "list":
            return await show_watchlist(ctx, user_id, include_menu_nav=True)

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
            result = await trigger_manual_stock_check(user_id)
            if result:
                return {
                    "text": f"✅ 股票行情已刷新。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result}",
                    "ui": _stock_home_ui(),
                }
            return {"text": "📭 您的自选股列表为空，无法刷新。", "ui": _stock_home_ui()}

        return {"text": _stock_usage_text(), "ui": {}}

    adapter_manager.on_command("stock", cmd_stock, description="自选股管理")

    # Callback
    adapter_manager.on_callback_query("^stock_", handle_stock_select_callback)
    adapter_manager.on_callback_query("^stkm_", handle_stock_select_callback)

    # "del_stock_" is handled by generic handle_subscription_callback in old code?
    # No, stock_del_ is in handle_stock_select_callback now (refactored previously).
    # Wait, previous refactor moved handle_stock_select_callback to handlers/stock_handlers.py
    # and it handles stock_del_.
    # Check handle_stock_select_callback below.


async def show_watchlist(
    ctx: UnifiedContext,
    user_id: int | str,
    *,
    include_menu_nav: bool = False,
) -> str:
    """显示自选股列表"""
    # Note: caller should handle permission if needed
    watchlist = await get_user_watchlist(user_id)

    if not watchlist:
        return {
            "text": "📭 **您的自选股为空**\n\n发送「帮我关注 XX股票」可添加自选股。",
            "ui": _stock_home_ui() if include_menu_nav else {},
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

    if include_menu_nav:
        actions.append(
            [
                {"text": "➕ 如何添加", "callback_data": make_callback(STOCK_MENU_NS, "addhelp")},
                {"text": "🏠 返回首页", "callback_data": make_callback(STOCK_MENU_NS, "home")},
            ]
        )

    return {"text": message, "ui": {"actions": actions}}


async def remove_stock(ctx: UnifiedContext, user_id: int, stock_name: str) -> str:
    """删除自选股"""
    watchlist = await get_user_watchlist(user_id)
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
    await _ensure_default_stock_delivery_target(ctx, user_id)

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
    await _ensure_default_stock_delivery_target(ctx, user_id)
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
    if not _stock_enabled(ctx):
        await ctx.reply(channel_feature_denied_text("stock"))
        return
    data = ctx.callback_data
    if not data:
        return

    action, parts = parse_callback(data, STOCK_MENU_NS)
    if action:
        await ctx.answer_callback()
        user_id = ctx.callback_user_id or ctx.message.user.id

        if action == "home":
            payload = await show_stock_menu(ctx, user_id)
        elif action == "list":
            payload = await show_watchlist(ctx, user_id, include_menu_nav=True)
        elif action == "refresh":
            result = await trigger_manual_stock_check(user_id)
            payload = {
                "text": (
                    f"✅ 股票行情已刷新。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result}"
                    if result
                    else "📭 您的自选股列表为空，无法刷新。"
                ),
                "ui": _stock_home_ui(),
            }
        elif action == "bind":
            target = _current_delivery_target(ctx)
            updated = await set_feature_delivery_target(
                user_id,
                "stock",
                target["platform"],
                target["chat_id"],
            )
            menu = await show_stock_menu(ctx, user_id)
            payload = {
                "text": (
                    "✅ 已把自选股推送渠道切换到当前聊天 "
                    f"`{_format_delivery_target(updated)}`。\n\n{menu['text']}"
                ),
                "ui": menu.get("ui"),
            }
        elif action == "addhelp":
            payload = _stock_add_help_response()
        elif action == "help":
            payload = {
                "text": _stock_usage_text(),
                "ui": {
                    "actions": [
                        [
                            {"text": "🏠 返回首页", "callback_data": make_callback(STOCK_MENU_NS, "home")},
                            {"text": "📋 查看自选股", "callback_data": make_callback(STOCK_MENU_NS, "list")},
                        ]
                    ]
                },
            }
        else:
            payload = {"text": "❌ 未知操作。", "ui": _stock_home_ui()}

        await ctx.edit_message(ctx.message.id, payload["text"], ui=payload.get("ui"))
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
            payload = await show_watchlist(ctx, user_id, include_menu_nav=True)
            await ctx.edit_message(
                ctx.message.id,
                f"✅ 已取消关注 {stock_code}\n\n{payload['text']}",
                ui=payload.get("ui"),
            )
        else:
            await ctx.edit_message(ctx.message.id, "❌ 删除失败")
        return


def _resolve_cli_user_id(explicit_user_id: str | None) -> str:
    raw = str(explicit_user_id or os.getenv("X_BOT_RUNTIME_USER_ID") or "").strip()
    if raw.startswith("subagent::"):
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
    if not platform or platform == "subagent_kernel":
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


from core.extension_base import SkillExtension


class StockWatchSkillExtension(SkillExtension):
    name = "stock_watch_extension"
    skill_name = "stock_watch"

    def register(self, runtime) -> None:
        register_handlers(runtime.adapter_manager)
        register_jobs(runtime.scheduler)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_cli()))
