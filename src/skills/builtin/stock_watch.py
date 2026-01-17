"""
自选股 Skill - 管理用户的股票关注列表
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from repositories import add_watchlist_stock, remove_watchlist_stock, get_user_watchlist
from services.stock_service import fetch_stock_quotes, format_stock_message, search_stock_by_name
from utils import smart_edit_text, smart_reply_text


SKILL_META = {
    "name": "stock_watch",
    "description": "管理用户自选股列表，支持添加、删除、查看股票",
    "triggers": ["关注", "自选股", "盯盘", "添加股票", "取消关注", "watch", "stock"],
    "params": {
        "action": {
            "type": "str",
            "enum": ["add", "remove", "list"],
            "description": "操作类型"
        },
        "stock_name": {
            "type": "str",
            "optional": True,
            "description": "股票名称"
        }
    },
    "version": "1.0.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    """执行自选股操作"""
    import re
    
    user_id = update.effective_user.id
    action = params.get("action", "list")
    stock_name = params.get("stock_name", "")
    
    if action == "list" or not stock_name:
        await _show_watchlist(update, user_id)
        return
    
    if action == "remove":
        await _remove_stock(update, user_id, stock_name)
        return
    
    # action == "add"
    # 支持多股票：用"和"、顿号、逗号分隔
    stock_names = re.split(r'[、,，和]+', stock_name.strip())
    stock_names = [s.strip() for s in stock_names if s.strip()]
    
    if not stock_names:
        await smart_reply_text(update, "❌ 请输入有效的股票名称")
        return
    
    if len(stock_names) == 1:
        await _add_single_stock(update, user_id, stock_names[0])
    else:
        await _add_multiple_stocks(update, user_id, stock_names)


async def _show_watchlist(update: Update, user_id: int) -> None:
    """显示自选股列表"""
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


async def _remove_stock(update: Update, user_id: int, stock_name: str) -> None:
    """删除自选股"""
    watchlist = await get_user_watchlist(user_id)
    for item in watchlist:
        if stock_name.lower() in item["stock_name"].lower():
            await remove_watchlist_stock(user_id, item["stock_code"])
            await smart_reply_text(update, f"✅ 已取消关注 **{item['stock_name']}**")
            return
    await smart_reply_text(update, f"⚠️ 未找到匹配「{stock_name}」的自选股")


async def _add_single_stock(update: Update, user_id: int, stock_name: str) -> None:
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
    
    # 多个结果，让用户选择
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


async def _add_multiple_stocks(update: Update, user_id: int, stock_names: list[str]) -> None:
    """批量添加多个股票"""
    msg = await smart_reply_text(update, f"🔍 正在搜索 {len(stock_names)} 只股票...")
    
    success_list = []
    failed_list = []
    existed_list = []
    
    for name in stock_names:
        results = await search_stock_by_name(name)
        
        if not results:
            failed_list.append(name)
        elif len(results) >= 1:
            stock = results[0]
            success = await add_watchlist_stock(user_id, stock["code"], stock["name"])
            if success:
                success_list.append(stock["name"])
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
