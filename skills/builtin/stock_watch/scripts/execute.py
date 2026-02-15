"""
Stock Watch Skill Script
"""

import re


from repositories import (
    remove_watchlist_stock_by_code,
    get_user_watchlist,
    add_watchlist_stock,
)
from services.stock_service import (
    fetch_stock_quotes,
    format_stock_message,
    search_stock_by_name,
)
from core.platform.models import UnifiedContext
import logging

logger = logging.getLogger(__name__)


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
        result = await trigger_manual_stock_check(ctx.platform_ctx, user_id)
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
    """注册 Stock 相关的 Command 和 Callback"""
    from core.config import is_user_allowed

    async def cmd_watchlist(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        return await show_watchlist(ctx, ctx.message.user.id)

    async def cmd_add_stock(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return

        args = []
        if ctx.message.text:
            parts = ctx.message.text.split()
            if len(parts) > 1:
                args = parts[1:]

        if args:
            name = " ".join(args)
            if "," in name or " " in name or "，" in name:
                names = [n.strip() for n in re.split(r"[,，\s]+", name) if n.strip()]
                return await add_multiple_stocks(ctx, ctx.message.user.id, names)
            else:
                return await add_single_stock(ctx, ctx.message.user.id, name)
        else:
            return "请使用: /add_stock <股票名称>"

    # Aliases
    adapter_manager.on_command("watchlist", cmd_watchlist, description="查看自选股行情")
    adapter_manager.on_command("stocks", cmd_watchlist, description="查看自选股行情")

    # Missing commands
    adapter_manager.on_command(
        "addstock", cmd_add_stock, description="添加自选股 (例: /addstock 茅台)"
    )

    async def cmd_del_stock(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return

        args = []
        if ctx.message.text:
            parts = ctx.message.text.split()
            if len(parts) > 1:
                args = parts[1:]

        if args:
            name = " ".join(args)
            return await remove_stock(ctx, ctx.message.user.id, name)
        else:
            return "请使用: /delstock <股票名称>"

    adapter_manager.on_command(
        "delstock", cmd_del_stock, description="删除自选股 (例: /delstock 茅台)"
    )
    # Optional implicit add via message? No, keep explicit commands for now

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
            await remove_watchlist_stock_by_code(user_id, item["stock_code"])
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
        success = await remove_watchlist_stock_by_code(user_id, stock_code)
        if success:
            await ctx.edit_message(ctx.message.id, f"✅ 已取消关注 {stock_code}")
        else:
            await ctx.edit_message(ctx.message.id, "❌ 删除失败")
        return
