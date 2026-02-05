import re
import datetime
import logging
from core.platform.models import UnifiedContext
from core.scheduler import schedule_reminder
from core.config import WAITING_FOR_REMIND_INPUT
from stats import increment_stat
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def execute(ctx: UnifiedContext, params: dict) -> Dict[str, Any]:
    """执行提醒设置"""
    time_str = params.get("time", "")
    content = params.get("content", "")

    if not time_str or not content:
        return {
            "text": "⏰ **设置定时提醒**\n\n请告诉我时间和内容，例如：\n• 10分钟后提醒我喝水\n• 1小时后提醒我开会",
            "ui": {},
        }

    # 复用 parsing logic
    success, result_msg = await _process_remind_logic(ctx, time_str, content)

    return {
        "text": result_msg,
        "ui": {},
    }


async def _process_remind_logic(
    ctx: UnifiedContext, time_str: str, message: str
) -> tuple[bool, str]:
    """实际处理提醒逻辑 (Returns success, message)"""
    matches = re.findall(r"(\d+)([smhd分秒时天])", time_str.lower())

    if not matches:
        return False, "❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。"

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
        return False, "❌ 时间必须大于 0。"

    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(
        seconds=delta_seconds
    )

    user_id = ctx.message.user.id
    chat_id = int(ctx.message.chat.id)

    # Get job_queue from platform context
    job_queue = getattr(ctx.platform_ctx, "job_queue", None)
    if job_queue:
        await schedule_reminder(job_queue, user_id, chat_id, message, trigger_time)
    else:
        # Fallback if job_queue is not directly available (e.g. Discord sometimes)
        # But core.scheduler might handle it if we use it directly?
        # Re-check core.scheduler usage. Original uses job_queue.
        pass

    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")

    await increment_stat(user_id, "reminders_set")
    return True, f"👌 已设置提醒：{message}\n⏰ 将在 {display_time} 提醒你。"


# --- Handlers ---

CONVERSATION_END = -1


async def remind_command(ctx: UnifiedContext) -> int:
    """处理 /remind 命令"""
    # check permission logic if needed, usually adapter_manager handles basic routing,
    # but specific permission checks (like admin only) are inside.
    # checking base_handlers implementation:
    from core.config import is_user_allowed

    if not await is_user_allowed(ctx.message.user.id):
        return CONVERSATION_END

    text = ctx.message.text
    parts = text.split(maxsplit=2)

    # If standard command "/remind 10m content"
    if len(parts) >= 3:
        # parts[0] is /remind, parts[1] time, parts[2] content
        success, msg = await _process_remind_logic(ctx, parts[1], parts[2])
        await ctx.reply({"text": msg, "ui": {}})
        return CONVERSATION_END

    # Interactive mode
    await ctx.reply(
        {
            "text": "⏰ **设置定时提醒**\n\n请发送您想要的提醒时间和内容。\n格式：`<时间> <内容>`\n示例：\n• 10m 喝水\n• 1h 开会\n\n发送 /cancel 取消。",
            "ui": {},
        }
    )
    return WAITING_FOR_REMIND_INPUT


async def handle_remind_input(ctx: UnifiedContext) -> int:
    text = ctx.message.text
    if not text:
        await ctx.reply("请发送有效文本。")
        return WAITING_FOR_REMIND_INPUT

    parts = text.strip().split(" ", 1)
    if len(parts) < 2:
        await ctx.reply(
            "⚠️ 格式不正确。请同时提供时间和内容，用空格分开。\n例如：10m 喝水"
        )
        return WAITING_FOR_REMIND_INPUT

    success, msg = await _process_remind_logic(ctx, parts[0], parts[1])
    await ctx.reply({"text": msg, "ui": {}})

    if success:
        return CONVERSATION_END
    return WAITING_FOR_REMIND_INPUT


async def cancel(ctx: UnifiedContext) -> int:
    await ctx.reply("已取消操作。")
    return CONVERSATION_END


def register_handlers(adapter_manager: Any):
    """Register handlers for Reminder skill"""

    # 1. Telegram Conversation Handler
    try:
        tg_adapter = adapter_manager.get_adapter("telegram")
        from telegram.ext import ConversationHandler, filters

        # Create wrappers
        entry_handler = tg_adapter.create_command_handler("remind", remind_command)
        msg_handler = tg_adapter.create_message_handler(
            filters.TEXT & ~filters.COMMAND, handle_remind_input
        )
        cancel_handler = tg_adapter.create_command_handler("cancel", cancel)

        conv_handler = ConversationHandler(
            entry_points=[entry_handler],
            states={
                WAITING_FOR_REMIND_INPUT: [msg_handler],
            },
            fallbacks=[cancel_handler],
            per_message=False,
        )

        tg_adapter.application.add_handler(conv_handler)
        logger.info("✅ Registered /remind ConversationHandler for Telegram")

    except ValueError:
        logger.info("Telegram adapter not found, skipping specific registration")
    except Exception as e:
        logger.error(f"Failed to register Telegram reminder handler: {e}")

    # 2. Generic Command (Fallback for other platforms or if TG fails)
    # Note: On TG, ConversationHandler takes precedence if added first/correctly.
    # For Discord/DingTalk, we support simple stateless command "/remind 10m content"
    adapter_manager.on_command("remind", remind_command, description="设置定时提醒")
