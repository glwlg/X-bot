import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from repositories.subscription_repo import delete_subscription_by_id, get_user_subscriptions
from repositories.watchlist_repo import remove_watchlist_stock, get_user_watchlist
from utils import smart_edit_text

from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)

async def handle_subscription_callback(ctx: UnifiedContext):
    """
    处理订阅管理的 Callback Query (删除订阅/删除自选股)
    """
    # Legacy fallback
    query = ctx.platform_event.callback_query
    await query.answer()
    
    data = query.data
    user_id = ctx.message.user.id
    # message is UnifiedMessage, but we need message_id for edit_message
    message_id = ctx.message.id
    
    try:
        if data.startswith("del_rss_"):
             # Format: del_rss_{id}
            sub_id = int(data.split("_")[-1])
            success = await delete_subscription_by_id(sub_id, user_id)
            if success:
                await query.answer("✅ 订阅已删除")
            else:
                await query.answer("❌ 删除失败，可能已不存在", show_alert=True)
                
        elif data.startswith("del_stock_"):
            # Format: del_stock_{code}
            stock_code = data.split("_")[-1]
            success = await remove_watchlist_stock(user_id, stock_code)
            if success:
                await query.answer("✅ 自选股已删除")
            else:
                await query.answer("❌ 删除失败", show_alert=True)
                
        # 无论成功与否，刷新列表
        await refresh_subscription_list_message(ctx, message_id, user_id)
        
    except Exception as e:
        logger.error(f"Error handling subscription callback: {e}")
        await query.answer("❌ 系统错误", show_alert=True)


async def refresh_subscription_list_message(ctx: UnifiedContext, message_id: str, user_id: int):
    """
    刷新订阅列表消息内容 (删除后更新 UI)
    """
    # 重新获取数据
    rss_subs = await get_user_subscriptions(user_id)
    stocks = await get_user_watchlist(user_id)
    
    if not rss_subs and not stocks:
        await ctx.edit_message(message_id, "📭 您当前没有任何订阅。")
        return

    # 重新构建文本和按钮
    text_lines = ["📋 **您的订阅列表**\n"]
    keyboard = []
    
    if rss_subs:
        text_lines.append(f"\n📢 **RSS 订阅 ({len(rss_subs)})**")
        temp_row = []
        for sub in rss_subs:
            # 文本行
            text_lines.append(f"- [{sub['title']}]({sub['feed_url']})")
            
            # Button (Short title)
            # Use strict truncation to fit 2 in a row
            short_title = sub['title'][:8] + ".." if len(sub['title']) > 8 else sub['title']
            btn = InlineKeyboardButton(f"❌ {short_title}", callback_data=f"del_rss_{sub['id']}")
            
            temp_row.append(btn)
            if len(temp_row) == 2:
                keyboard.append(temp_row)
                temp_row = []
        if temp_row:
            keyboard.append(temp_row)
            
    if stocks:
        text_lines.append(f"\n📈 **自选股 ({len(stocks)})**")
        temp_row = []
        for s in stocks:
            text_lines.append(f"- {s['stock_name']} (`{s['stock_code']}`)")
            
            short_name = s['stock_name'][:8] + ".." if len(s['stock_name']) > 8 else s['stock_name']
            btn = InlineKeyboardButton(f"❌ {short_name}", callback_data=f"del_stock_{s['stock_code']}")
            
            temp_row.append(btn)
            if len(temp_row) == 2:
                keyboard.append(temp_row)
                temp_row = []
        if temp_row:
            keyboard.append(temp_row)
            
    final_text = "\n".join(text_lines)
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await ctx.edit_message(message_id, final_text, reply_markup=reply_markup)
