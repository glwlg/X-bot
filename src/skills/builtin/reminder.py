"""
提醒 Skill - 设置定时提醒
"""
import re
import datetime
from telegram import Update
from telegram.ext import ContextTypes

from core.scheduler import schedule_reminder
from stats import increment_stat
from utils import smart_reply_text


SKILL_META = {
    "name": "reminder",
    "description": "设置定时提醒，支持 10m/1h/30s 等时间格式",
    "triggers": ["提醒", "remind", "timer", "定时", "闹钟", "alarm"],
    "params": {
        "time": {
            "type": "str",
            "description": "时间间隔，如 10m, 1h, 30s"
        },
        "content": {
            "type": "str",
            "description": "提醒内容"
        }
    },
    "version": "1.0.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    """执行提醒设置"""
    time_str = params.get("time", "")
    content = params.get("content", "")
    
    if not time_str or not content:
        await smart_reply_text(update,
            "⏰ **设置定时提醒**\n\n"
            "请告诉我时间和内容，例如：\n"
            "• 10分钟后提醒我喝水\n"
            "• 1小时后提醒我开会"
        )
        return
    
    # 解析时间
    matches = re.findall(r"(\d+)([smhd分秒时天])", time_str.lower())
    
    if not matches:
        await smart_reply_text(update, "❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。")
        return
    
    delta_seconds = 0
    for value, unit in matches:
        value = int(value)
        if unit in ['s', '秒']:
            delta_seconds += value
        elif unit in ['m', '分']:
            delta_seconds += value * 60
        elif unit in ['h', '时']:
            delta_seconds += value * 3600
        elif unit in ['d', '天']:
            delta_seconds += value * 86400
    
    if delta_seconds <= 0:
        await smart_reply_text(update, "❌ 时间必须大于 0。")
        return
    
    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=delta_seconds)
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await schedule_reminder(context.job_queue, user_id, chat_id, content, trigger_time)
    
    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
    
    await smart_reply_text(update,
        f"👌 已设置提醒：{content}\n"
        f"⏰ 将在 {display_time} 提醒你。"
    )
    await increment_stat(user_id, "reminders_set")
