"""
服务 handlers - 向后兼容层

重新导出各子模块中的函数，保持现有代码的兼容性。
新代码推荐直接从对应子模块导入。
"""
import logging
from repositories import get_user_settings, set_translation_mode
from stats import get_user_stats_text
from .base_handlers import check_permission_unified
from core.platform.models import UnifiedContext

# 从子模块导入
from .reminder_handlers import (
    remind_command,
    handle_remind_input,
    process_remind,
)
from .subscription_handlers import (
    subscribe_command,
    handle_subscribe_input,
    process_subscribe,
    unsubscribe_command,
    handle_unsubscribe_callback,
    monitor_command,
    handle_monitor_input,
    process_monitor,
    list_subs_command,
)
from .feature_handlers import (
    feature_command,
    handle_feature_input,
    save_feature_command,
)
from .stock_handlers import (
    watchlist_command,
    process_stock_watch,
    handle_stock_select_callback,
)

logger = logging.getLogger(__name__)


# --- Stats (保留在此文件中，较小) ---

async def stats_command(ctx: UnifiedContext) -> None:
    """处理 /stats 命令"""
    if not await check_permission_unified(ctx):
        return

    user_id = ctx.message.user.id
    try:
         uid_int = int(user_id)
         stats_text = await get_user_stats_text(uid_int)
    except:
         stats_text = "Stats not available for non-numeric ID yet"

    await ctx.reply(stats_text)


# --- Translation (保留在此文件中，较小) ---

async def toggle_translation_command(ctx: UnifiedContext) -> None:
    """处理 /translate 命令，切换沉浸式翻译模式"""
    if not await check_permission_unified(ctx):
        return

    user_id = int(ctx.message.user.id) # Settings use int IDs
    
    settings = await get_user_settings(user_id)
    current_status = settings.get("auto_translate", 0)
    
    new_status = not current_status
    await set_translation_mode(user_id, new_status)
    
    if new_status:
        await ctx.reply(
            "🌍 **沉浸式翻译模式：已开启**\n\n"
            "现在发送任何文本消息，我都会为您自动翻译。\n"
            "• 外语 -> 中文\n"
            "• 中文 -> 英文\n\n"
            "再次输入 /translate 可关闭。"
        )
    else:
        await ctx.reply(
            "🚫 **沉浸式翻译模式：已关闭**\n\n"
            "已恢复正常 AI 助手模式。"
        )


# 导出所有函数
__all__ = [
    # Stats & Translation
    "stats_command",
    "toggle_translation_command",
    # Reminder
    "remind_command",
    "handle_remind_input",
    "process_remind",
    # Subscription
    "subscribe_command",
    "handle_subscribe_input",
    "process_subscribe",
    "unsubscribe_command",
    "handle_unsubscribe_callback",
    "monitor_command",
    "handle_monitor_input",
    "process_monitor",
    "list_subs_command",
    # Feature
    "feature_command",
    "handle_feature_input",
    "save_feature_command",
    # Stock
    "watchlist_command",
    "process_stock_watch",
    "handle_stock_select_callback",
]
