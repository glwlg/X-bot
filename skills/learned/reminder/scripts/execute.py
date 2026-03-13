from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from core.config import WAITING_FOR_REMIND_INPUT
from core.scheduler import schedule_reminder
from stats import increment_stat

logger = logging.getLogger(__name__)


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> Dict[str, Any]:
    """执行提醒设置"""
    time_str = params.get("time", "")
    content = params.get("content", "")

    if not time_str or not content:
        return {
            "text": "⏰ **设置定时提醒**\n\n请告诉我时间和内容，例如：\n• 10分钟后提醒我喝水\n• 1小时后提醒我开会",
            "ui": {},
        }

    # 复用 parsing logic
    result_data = await _process_remind_logic(ctx, time_str, content)

    # Return structured data (JSON) directly for Agent consumption
    # Agent will use this data to formulate its own reply.
    return result_data


async def _process_remind_logic(
    ctx: UnifiedContext, time_str: str, message: str
) -> Dict[str, Any]:
    """实际处理提醒逻辑 (Returns dict with text/ui)"""
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
    logger.info(ctx.message)
    chat_id = int(ctx.message.chat.id)

    # Get platform from context
    platform = ctx.message.platform or "telegram"

    # Use core.scheduler.schedule_reminder (Global Scheduler)
    # Correct signature: user_id, chat_id, message, trigger_time, platform
    await schedule_reminder(user_id, chat_id, message, trigger_time, platform=platform)

    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")

    await increment_stat(user_id, "reminders_set")

    return {
        "text": f"👌 已设置提醒：{message}\n⏰ 将在 {display_time} 提醒你。",
        "ui": {},
    }


# --- Handlers ---

CONVERSATION_END = -1


def _parse_remind_command(text: str) -> tuple[str, str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "help", "", ""

    parts = raw.split(maxsplit=2)
    if not parts:
        return "help", "", ""
    if not parts[0].startswith("/remind"):
        return "help", "", ""
    if len(parts) == 1:
        return "help", "", ""

    sub = str(parts[1] or "").strip().lower()
    if sub in {"help", "h", "?"}:
        return "help", "", ""
    if len(parts) < 3:
        return "help", "", ""

    return "set", str(parts[1]).strip(), str(parts[2]).strip()


def _remind_usage_text() -> str:
    return (
        "⏰ **定时提醒使用帮助**\n\n"
        "用法：\n"
        "• `/remind <时间> <内容>`\n"
        "• `/remind help`\n\n"
        "示例：\n"
        "• `/remind 10m 喝水`\n"
        "• `/remind 1h30m 休息一下`\n\n"
        "时间单位支持：s(秒), m(分), h(时), d(天)"
    )


async def remind_command(ctx: UnifiedContext) -> int:
    """处理 /remind 命令"""
    # check permission logic if needed, usually adapter_manager handles basic routing,
    # but specific permission checks (like admin only) are inside.
    # checking base_handlers implementation:
    from core.config import is_user_allowed

    if not await is_user_allowed(ctx.message.user.id):
        return CONVERSATION_END

    mode, time_str, content = _parse_remind_command(ctx.message.text or "")
    if mode == "set":
        result = await _process_remind_logic(ctx, time_str, content)
        await ctx.reply(result.get("text"))
        return CONVERSATION_END

    await ctx.reply({"text": _remind_usage_text(), "ui": {}})
    return CONVERSATION_END


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

    result = await _process_remind_logic(ctx, parts[0], parts[1])
    await ctx.reply(result.get("text"))

    # Check for success based on text content
    if "❌" in result.get("text", "") or "⚠️" in result.get("text", ""):
        return WAITING_FOR_REMIND_INPUT

    return CONVERSATION_END


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reminder skill CLI bridge.",
    )
    add_common_arguments(parser)
    parser.add_argument("time", help="Relative time such as 10m or 1h30m")
    parser.add_argument("content", help="Reminder content")
    return parser


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    params = merge_params(
        args,
        {
            "time": str(args.time or "").strip(),
            "content": str(args.content or "").strip(),
        },
    )
    return await run_execute_cli(execute, args=args, params=params)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
