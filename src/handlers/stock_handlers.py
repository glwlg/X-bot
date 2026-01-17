"""
自选股功能 handlers
"""
import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from repositories import add_watchlist_stock, remove_watchlist_stock, get_user_watchlist
from services.stock_service import fetch_stock_quotes, format_stock_message, search_stock_by_name
from .base_handlers import check_permission
from utils import smart_edit_text, smart_reply_text

logger = logging.getLogger(__name__)


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /watchlist 命令，显示自选股列表"""
    if not await check_permission(update):
        return

    user_id = update.effective_user.id
    watchlist = await get_user_watchlist(user_id)
    
    if not watchlist:
        await smart_reply_text(update,
            "📭 **您的自选股为空**\n\n"
            "发送「帮我关注 XX股票」可添加自选股。"
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
        keyboard.append([InlineKeyboardButton(
            f"❌ 删除 {item['stock_name']}", 
            callback_data=f"stock_del_{item['stock_code']}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await smart_reply_text(update, message, reply_markup=reply_markup)


async def process_stock_watch(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, stock_name: str) -> None:
    """
    处理自选股操作
    - action=add: 搜索股票，若唯一则直接添加，若多个则展示按钮让用户选择
    - action=remove: 删除指定股票
    - action=list: 显示列表
    """
    if not await check_permission(update):
        return
    
    user_id = update.effective_user.id
    
    if action == "list" or not stock_name:
        await watchlist_command(update, context)
        return
    
    if action == "remove":
        watchlist = await get_user_watchlist(user_id)
        for item in watchlist:
            if stock_name.lower() in item["stock_name"].lower():
                await remove_watchlist_stock(user_id, item["stock_code"])
                await smart_reply_text(update, f"✅ 已取消关注 **{item['stock_name']}**")
                return
        await smart_reply_text(update, f"⚠️ 未找到匹配「{stock_name}」的自选股")
        return
    
    # action == "add": 添加操作
    stock_names = re.split(r'[、,，和]+', stock_name.strip())
    stock_names = [s.strip() for s in stock_names if s.strip()]
    
    if not stock_names:
        await smart_reply_text(update, "❌ 请输入有效的股票名称")
        return
    
    if len(stock_names) == 1:
        await _add_single_stock(update, context, user_id, stock_names[0])
    else:
        msg = await smart_reply_text(update, f"🔍 正在搜索 {len(stock_names)} 只股票...")
        
        success_list = []
        failed_list = []
        existed_list = []
        
        for name in stock_names:
            results = await search_stock_by_name(name)
            
            if not results:
                failed_list.append(name)
            elif len(results) == 1:
                stock = results[0]
                success = await add_watchlist_stock(user_id, stock["code"], stock["name"])
                if success:
                    success_list.append(stock["name"])
                else:
                    existed_list.append(stock["name"])
            else:
                stock = results[0]
                success = await add_watchlist_stock(user_id, stock["code"], stock["name"])
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
            "**自选股添加完成！**\n\n" +
            "\n".join(result_parts) +
            "\n\n交易时段将每 10 分钟推送行情。"
        )
        
        await smart_edit_text(msg, result_msg)


async def _add_single_stock(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, stock_name: str) -> None:
    """添加单个股票"""
    msg = await smart_reply_text(update, f"🔍 正在搜索「{stock_name}」...")
    
    results = await search_stock_by_name(stock_name)
    
    if not results:
        await smart_edit_text(msg, f"❌ 未找到匹配「{stock_name}」的股票")
        return
    
    if len(results) == 1:
        stock = results[0]
        success = await add_watchlist_stock(user_id, stock["code"], stock["name"])
        if success:
            await smart_edit_text(msg, 
                f"✅ 已添加自选股\n\n"
                f"**{stock['name']}** ({stock['code']})\n\n"
                f"交易时段将每 10 分钟推送行情。"
            )
        else:
            await smart_edit_text(msg, f"⚠️ **{stock['name']}** 已在您的自选股中")
        return
    
    keyboard = []
    for stock in results[:8]:
        keyboard.append([InlineKeyboardButton(
            f"{stock['name']} ({stock['code']}) - {stock['market']}", 
            callback_data=f"stock_add_{stock['code']}_{stock['name']}"
        )])
    keyboard.append([InlineKeyboardButton("🚫 取消", callback_data="stock_cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await smart_edit_text(msg, 
        f"🔍 找到多个匹配「{stock_name}」的股票，请选择：",
        reply_markup=reply_markup
    )


async def handle_stock_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理用户点击选择股票的回调"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    if data == "stock_cancel":
        await query.edit_message_text("👌 已取消操作。")
        return
    
    if data.startswith("stock_add_"):
        parts = data.replace("stock_add_", "").split("_", 1)
        if len(parts) == 2:
            stock_code, stock_name = parts
            success = await add_watchlist_stock(user_id, stock_code, stock_name)
            if success:
                await query.edit_message_text(
                    f"✅ 已添加自选股\n\n"
                    f"**{stock_name}** ({stock_code})\n\n"
                    f"交易时段将每 10 分钟推送行情。"
                )
            else:
                await query.edit_message_text(f"⚠️ **{stock_name}** 已在您的自选股中")
        return
    
    if data.startswith("stock_del_"):
        stock_code = data.replace("stock_del_", "")
        success = await remove_watchlist_stock(user_id, stock_code)
        if success:
            await query.edit_message_text(f"✅ 已取消关注 {stock_code}")
        else:
            await query.edit_message_text("❌ 删除失败")
        return
