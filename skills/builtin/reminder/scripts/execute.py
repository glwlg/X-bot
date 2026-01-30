import re
import datetime
from core.platform.models import UnifiedContext
from core.scheduler import schedule_reminder
from stats import increment_stat
from utils import smart_reply_text

async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行提醒设置"""
    time_str = params.get("time", "")
    content = params.get("content", "")
    
    if not time_str or not content:
        await ctx.reply(
            "⏰ **设置定时提醒**\n\n"
            "请告诉我时间和内容，例如：\n"
            "• 10分钟后提醒我喝水\n"
            "• 1小时后提醒我开会"
        )
        return "❌ 未提供时间或内容"
    
    # 解析时间
    matches = re.findall(r"(\d+)([smhd分秒时天])", time_str.lower())
    
    if not matches:
        await ctx.reply("❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。")
        return "❌ 时间格式错误"
    
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
        await ctx.reply("❌ 时间必须大于 0。")
        return "❌ 时间必须大于0"
    
    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=delta_seconds)
    
    user_id = int(ctx.message.user.id)
    chat_id = int(ctx.message.chat.id)
    
    # Get job_queue from platform context
    job_queue = getattr(ctx.platform_ctx, "job_queue", None)
    if job_queue:
        await schedule_reminder(job_queue, user_id, chat_id, content, trigger_time)
    else:
        return "❌ 提醒设置失败: JobQueue 不可用 (Platform limit)"
    
    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
    
    await ctx.reply(
        f"👌 已设置提醒：{content}\n"
        f"⏰ 将在 {display_time} 提醒你。"
    )
    await increment_stat(user_id, "reminders_set")
    return f"✅ 提醒设置成功: {content} at {display_time}"
