"""
自选股功能 handlers
"""

import re
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from repositories import add_watchlist_stock, remove_watchlist_stock, get_user_watchlist
from services.stock_service import (
    fetch_stock_quotes,
    format_stock_message,
    search_stock_by_name,
)
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)


async def watchlist_command(ctx: UnifiedContext) -> None:
    """处理 /watchlist 命令，显示自选股列表"""
    # Check permission using helper or assuming middleware checked it?
    # For now, simplistic check
    from core.config import is_user_allowed

    if not await is_user_allowed(ctx.message.user.id):
        return

    user_id = ctx.message.user.id
    platform = ctx.message.platform if ctx.message.platform else "telegram"
    watchlist = await get_user_watchlist(user_id, platform=platform)

    if not watchlist:
        await ctx.reply(
            "📭 **您的自选股为空**\n\n发送「帮我关注 XX股票」可添加自选股。"
        )
        return

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
    for item in watchlist:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"❌ 删除 {item['stock_name']}",
                    callback_data=f"stock_del_{item['stock_code']}",
                )
            ]
        )

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await ctx.reply(message, reply_markup=reply_markup)


async def process_stock_watch(
    ctx: UnifiedContext, action: str, stock_name: str
) -> None:
    """
    处理自选股操作
    - action=add: 搜索股票，若唯一则直接添加，若多个则展示按钮让用户选择
    - action=remove: 删除指定股票
    - action=list: 显示列表
    """
    from core.config import is_user_allowed

    if not await is_user_allowed(ctx.message.user.id):
        return

    user_id = ctx.message.user.id
    platform = ctx.message.platform if ctx.message.platform else "telegram"

    if action == "list" or not stock_name:
        await watchlist_command(ctx)
        return

    if action == "remove":
        watchlist = await get_user_watchlist(user_id, platform=platform)
        for item in watchlist:
            if stock_name.lower() in item["stock_name"].lower():
                await remove_watchlist_stock(user_id, item["stock_code"])
                await ctx.reply(f"✅ 已取消关注 **{item['stock_name']}**")
                return
        await ctx.reply(f"⚠️ 未找到匹配「{stock_name}」的自选股")
        return

    # action == "add": 添加操作
    stock_names = re.split(r"[、,，和]+", stock_name.strip())
    stock_names = [s.strip() for s in stock_names if s.strip()]

    if not stock_names:
        await ctx.reply("❌ 请输入有效的股票名称")
        return

    if len(stock_names) == 1:
        await _add_single_stock(ctx, user_id, stock_names[0])
    else:
        msg = await ctx.reply(f"🔍 正在搜索 {len(stock_names)} 只股票...")

        success_list = []
        failed_list = []
        existed_list = []

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
                    success_list.append(
                        f"{stock['name']}(自动匹配)"
                    )  # This comment from original file might be missing in snippet but context should match
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

        result_msg = (
            "**自选股添加完成！**\n\n"
            + "\n".join(result_parts)
            + "\n\n交易时段将每 10 分钟推送行情。"
        )

        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)), result_msg
        )


async def _add_single_stock(ctx: UnifiedContext, user_id: int, stock_name: str) -> None:
    """添加单个股票"""
    msg = await ctx.reply(f"🔍 正在搜索「{stock_name}」...")

    results = await search_stock_by_name(stock_name)
    platform = ctx.message.platform if ctx.message.platform else "telegram"

    if not results:
        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)),
            f"❌ 未找到匹配「{stock_name}」的股票",
        )
        return

    if len(results) == 1:
        stock = results[0]
        success = await add_watchlist_stock(
            user_id, stock["code"], stock["name"], platform=platform
        )
        if success:
            await ctx.edit_message(
                getattr(msg, "message_id", getattr(msg, "id", None)),
                f"✅ 已添加自选股\n\n"
                f"**{stock['name']}** ({stock['code']})\n\n"
                f"交易时段将每 10 分钟推送行情。",
            )
        else:
            await ctx.edit_message(
                getattr(msg, "message_id", getattr(msg, "id", None)),
                f"⚠️ **{stock['name']}** 已在您的自选股中",
            )
        return

    keyboard = []
    for stock in results[:8]:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{stock['name']} ({stock['code']}) - {stock['market']}",
                    callback_data=f"stock_add_{stock['code']}_{stock['name']}",
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("🚫 取消", callback_data="stock_cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await ctx.edit_message(
        getattr(msg, "message_id", getattr(msg, "id", None)),
        f"🔍 找到多个匹配「{stock_name}」的股票，请选择：",
        reply_markup=reply_markup,
    )


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
