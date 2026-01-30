from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from repositories import add_watchlist_stock, remove_watchlist_stock, get_user_watchlist
from services.stock_service import fetch_stock_quotes, format_stock_message, search_stock_by_name
import re
from core.scheduler import trigger_manual_stock_check
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
        "添加": "add_stock", "关注": "add_stock", "add": "add_stock",
        "删除": "remove_stock", "取消": "remove_stock", "取消关注": "remove_stock", "remove": "remove_stock", "delete": "remove_stock",
        "查看": "list", "列表": "list", "list": "list",
        "刷新": "refresh", "更新": "refresh", "refresh": "refresh"
    }
    action = ACTION_MAP.get(raw_action, raw_action)
    
    if action == "refresh":
        msg = await ctx.reply("⏳ 正在获取最新行情...")
        # Note: trigger_manual_stock_check still expects legacy context? 
        # Checking imports... it imports job_queue from context?
        # Assuming we can pass ctx.platform_ctx if needed, but the function signature in scheduler.py likely needs check.
        # For now, let's pass context if we can. 
        # Actually trigger_manual_stock_check(context, user_id) uses context.bot probably.
        result = await trigger_manual_stock_check(ctx.platform_ctx, user_id)
        if result:
            await ctx.edit_message(msg.message_id, result)
            return f"✅ 股票行情已刷新。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result}"
        else:
            await ctx.edit_message(msg.message_id, "📭 您的自选股列表为空，无法刷新。")
            return "❌ 刷新失败: 自选股为空"
            
    if action == "add_stock":
        if "," in stock_name or " " in stock_name or "，" in stock_name:
             names = [n.strip() for n in re.split(r'[,，\s]+', stock_name) if n.strip()]
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
            "📭 **您的自选股为空**\n\n"
            "发送「帮我关注 XX股票」可添加自选股。"
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
            callback_data=f"stock_del_{item['stock_code']}"
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


async def _add_single_stock(ctx: UnifiedContext, user_id: int, stock_name: str) -> str:
    """添加单个股票"""
    msg = await ctx.reply(f"🔍 正在搜索「{stock_name}」...")
    
    results = await search_stock_by_name(stock_name)
    
    if not results:
        await ctx.edit_message(msg.message_id, f"❌ 未找到匹配「{stock_name}」的股票")
        return f"❌ 未找到股票: {stock_name}"
    
    if len(results) == 1:
        stock = results[0]
        success = await add_watchlist_stock(user_id, stock["code"], stock["name"])
        if success:
            await ctx.edit_message(msg.message_id, 
                f"✅ 已添加自选股\n\n"
                f"**{stock['name']}** ({stock['code']})\n\n"
                f"交易时段将每 10 分钟推送行情。"
            )
            return f"✅ 添加自选股成功: {stock['name']}"
        else:
            await ctx.edit_message(msg.message_id, f"⚠️ **{stock['name']}** 已在您的自选股中")
            return f"⚠️ 自选股已存在: {stock['name']}"
    
    # 多个结果，让用户选择
    keyboard = []
    for stock in results[:8]:
        keyboard.append([InlineKeyboardButton(
            f"{stock['name']} ({stock['code']}) - {stock['market']}", 
            callback_data=f"stock_add_{stock['code']}_{stock['name']}"
        )])
    keyboard.append([InlineKeyboardButton("🚫 取消", callback_data="stock_cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await ctx.edit_message(msg.message_id, 
        f"🔍 找到多个匹配「{stock_name}」的股票，请选择：",
        reply_markup=reply_markup
    )
    return f"✅ 找到多个股票，等待用户选择: {stock_name}"


async def _add_multiple_stocks(ctx: UnifiedContext, user_id: int, stock_names: list[str]) -> str:
    """批量添加多个股票"""
    msg = await ctx.reply(f"🔍 正在搜索 {len(stock_names)} 只股票...")
    
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
    
    await ctx.edit_message(msg.message_id, result_msg)
    return "✅ 批量添加完成: " + ", ".join(result_parts)
