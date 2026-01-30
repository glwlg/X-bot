"""
提醒功能 handlers
"""
import re
import logging
import datetime
from core.platform.models import UnifiedContext
from telegram.ext import ConversationHandler

from core.config import WAITING_FOR_REMIND_INPUT
from core.scheduler import schedule_reminder
from stats import increment_stat
from .base_handlers import check_permission_unified

logger = logging.getLogger(__name__)


async def remind_command(ctx: UnifiedContext) -> int:
    """处理 /remind 命令，支持交互式输入"""
    if not await check_permission_unified(ctx):
        return ConversationHandler.END
    
    if not ctx.platform_ctx:
         return ConversationHandler.END

    args = ctx.platform_ctx.args
    if args and len(args) >= 2:
        await process_remind(ctx, args[0], " ".join(args[1:]))
        return ConversationHandler.END
        
    await ctx.reply(
        "⏰ **设置定时提醒**\n\n"
        "请发送您想要的提醒时间和内容。\n"
        "格式：`<时间> <内容>`\n\n"
        "示例：\n"
        "• 10m 喝水\n"
        "• 1h30m 开会\n"
        "• 20s 测试一下\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_REMIND_INPUT


async def handle_remind_input(ctx: UnifiedContext) -> int:
    """处理提醒的交互式输入"""
    text = ctx.message.text
    if not text:
        await ctx.reply("请发送有效文本。")
        return WAITING_FOR_REMIND_INPUT
        
    parts = text.strip().split(" ", 1)
    if len(parts) < 2:
        await ctx.reply(
            "⚠️ 格式不正确。请同时提供时间和内容，用空格分开。\n"
            "例如：10m 喝水"
        )
        return WAITING_FOR_REMIND_INPUT
        
    success = await process_remind(ctx, parts[0], parts[1])
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_REMIND_INPUT


async def process_remind(ctx: UnifiedContext, time_str: str, message: str) -> bool:
    """实际处理提醒逻辑"""
    matches = re.findall(r"(\d+)([smhd])", time_str.lower())
    
    if not matches:
        await ctx.reply("❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。")
        return False
        
    delta_seconds = 0
    for value, unit in matches:
        value = int(value)
        if unit == 's':
            delta_seconds += value
        elif unit == 'm':
            delta_seconds += value * 60
        elif unit == 'h':
            delta_seconds += value * 3600
        elif unit == 'd':
            delta_seconds += value * 86400
            
    if delta_seconds <= 0:
        await ctx.reply("❌ 时间必须大于 0。")
        return False
        
    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=delta_seconds)
    
    user_id = ctx.message.user.id
    chat_id = ctx.message.chat.id
    
    if ctx.platform_ctx:
         await schedule_reminder(ctx.platform_ctx.job_queue, user_id, chat_id, message, trigger_time)
    
    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
        
    await ctx.reply(
        f"👌 已设置提醒：{message}\n"
        f"⏰ 将在 {display_time} 提醒你。"
    )
    # Using int ID for tracking stats
    try:
        uid_int = int(user_id)
        await increment_stat(uid_int, "reminders_set")
    except:
        pass
    return True
