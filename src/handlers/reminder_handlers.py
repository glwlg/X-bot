"""
提醒功能 handlers
"""
import re
import logging
import datetime
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from core.config import WAITING_FOR_REMIND_INPUT
from core.scheduler import schedule_reminder
from stats import increment_stat
from .base_handlers import check_permission
from utils import smart_reply_text

logger = logging.getLogger(__name__)


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /remind 命令，支持交互式输入"""
    if not await check_permission(update):
        return ConversationHandler.END

    args = context.args
    if args and len(args) >= 2:
        await process_remind(update, context, args[0], " ".join(args[1:]))
        return ConversationHandler.END
        
    await smart_reply_text(update,
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


async def handle_remind_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理提醒的交互式输入"""
    text = update.message.text
    if not text:
        await update.message.reply_text("请发送有效文本。")
        return WAITING_FOR_REMIND_INPUT
        
    parts = text.strip().split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ 格式不正确。请同时提供时间和内容，用空格分开。\n"
            "例如：10m 喝水"
        )
        return WAITING_FOR_REMIND_INPUT
        
    success = await process_remind(update, context, parts[0], parts[1])
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_REMIND_INPUT


async def process_remind(update: Update, context: ContextTypes.DEFAULT_TYPE, time_str: str, message: str) -> bool:
    """实际处理提醒逻辑"""
    matches = re.findall(r"(\d+)([smhd])", time_str.lower())
    
    if not matches:
        await smart_reply_text(update, "❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。")
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
        await smart_reply_text(update, "❌ 时间必须大于 0。")
        return False
        
    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=delta_seconds)
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await schedule_reminder(context.job_queue, user_id, chat_id, message, trigger_time)
    
    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
        
    await smart_reply_text(update,
        f"👌 已设置提醒：{message}\n"
        f"⏰ 将在 {display_time} 提醒你。"
    )
    await increment_stat(user_id, "reminders_set")
    return True
