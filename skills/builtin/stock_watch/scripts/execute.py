from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from repositories import remove_watchlist_stock, get_user_watchlist
from services.stock_service import (
    fetch_stock_quotes,
    format_stock_message,
)
from handlers.stock_handlers import _add_single_stock, _add_multiple_stocks
from core.platform.models import UnifiedContext


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行自选股操作"""
    import re
    from core.scheduler import trigger_manual_stock_check

    user_id = int(ctx.message.user.id)
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
        msg = await ctx.reply("⏳ 正在获取最新行情...")
        result = await trigger_manual_stock_check(ctx.platform_ctx, user_id)
        if result:
            await ctx.edit_message(
                getattr(msg, "message_id", getattr(msg, "id", None)), result
            )
            return f"✅ 股票行情已刷新。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result}"
        else:
            await ctx.edit_message(
                getattr(msg, "message_id", getattr(msg, "id", None)),
                "📭 您的自选股列表为空，无法刷新。",
            )
            return "❌ 刷新失败: 自选股为空"

    if action == "add_stock":
        if "," in stock_name or " " in stock_name or "，" in stock_name:
            names = [n.strip() for n in re.split(r"[,，\s]+", stock_name) if n.strip()]
            return await _add_multiple_stocks(ctx, user_id, names)
        else:
            return await _add_single_stock(ctx, user_id, stock_name)

    if action == "remove_stock":
        return await _remove_stock(ctx, user_id, stock_name)

    if action == "list" or not stock_name:
        return await _show_watchlist(ctx, user_id)


async def _show_watchlist(ctx: UnifiedContext, user_id: int) -> str:
    """显示自选股列表"""
    watchlist = await get_user_watchlist(user_id)

    if not watchlist:
        await ctx.reply(
            "📭 **您的自选股为空**\n\n发送「帮我关注 XX股票」可添加自选股。"
        )
        return "📭 自选股为空"

    stock_codes = [item["stock_code"] for item in watchlist]
    quotes = await fetch_stock_quotes(stock_codes)

    if quotes:
        message = format_stock_message(quotes)
    else:
        lines = ["📈 **我的自选股**\n"]
        for item in watchlist:
            lines.append(f"• {item['stock_name']} ({item['stock_code']})")
        message = "\n".join(lines)

    keyboard = []
    temp_row = []
    for item in watchlist:
        btn = InlineKeyboardButton(
            f"❌ {item['stock_name']}",
            callback_data=f"stock_del_{item['stock_code']}",
        )
        temp_row.append(btn)

        if len(temp_row) == 2:
            keyboard.append(temp_row)
            temp_row = []

    if temp_row:
        keyboard.append(temp_row)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await ctx.reply(message, reply_markup=reply_markup)
    return f"✅ 自选股列表已发送。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{message}"


async def _remove_stock(ctx: UnifiedContext, user_id: int, stock_name: str) -> str:
    """删除自选股"""
    watchlist = await get_user_watchlist(user_id)
    for item in watchlist:
        if stock_name.lower() in item["stock_name"].lower():
            await remove_watchlist_stock(user_id, item["stock_code"])
            await ctx.reply(f"✅ 已取消关注 **{item['stock_name']}**")
            return f"✅ 取消关注成功: {item['stock_name']}"
    await ctx.reply(f"⚠️ 未找到匹配「{stock_name}」的自选股")
    return f"❌ 未找到匹配股票: {stock_name}"
