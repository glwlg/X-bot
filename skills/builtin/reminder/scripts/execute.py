import re
import datetime
from core.platform.models import UnifiedContext
from core.scheduler import schedule_reminder
from stats import increment_stat


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行提醒设置"""
    time_str = params.get("time", "")
    content = params.get("content", "")

    if not time_str or not content:
        return {
            "text": "⏰ **设置定时提醒**\n\n请告诉我时间和内容，例如：\n• 10分钟后提醒我喝水\n• 1小时后提醒我开会",
            "ui": {},
        }

    # 解析时间
    matches = re.findall(r"(\d+)([smhd分秒时天])", time_str.lower())

    if not matches:
        return {"text": "❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。", "ui": {}}

    delta_seconds = 0
    for value, unit in matches:
        value = int(value)
        if unit in ["s", "秒"]:
            delta_seconds += value
        elif unit in ["m", "分"]:
            delta_seconds += value * 60
        elif unit in ["h", "时"]:
            delta_seconds += value * 3600
        elif unit in ["d", "天"]:
            delta_seconds += value * 86400

    if delta_seconds <= 0:
        return {"text": "❌ 时间必须大于 0。", "ui": {}}

    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(
        seconds=delta_seconds
    )

    user_id = ctx.message.user.id
    chat_id = int(ctx.message.chat.id)

    # Get job_queue from platform context
    job_queue = getattr(ctx.platform_ctx, "job_queue", None)
    if job_queue:
        await schedule_reminder(job_queue, user_id, chat_id, content, trigger_time)
    else:
        return {"text": "❌ 提醒设置失败: JobQueue 不可用 (Platform limit)", "ui": {}}

    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")

    await increment_stat(user_id, "reminders_set")
    return {
        "text": f"👌 已设置提醒：{content}\n⏰ 将在 {display_time} 提醒你。",
        "ui": {},
    }
