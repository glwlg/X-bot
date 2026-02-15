"""
服务 handlers - 向后兼容层

重新导出各子模块中的函数，保持现有代码的兼容性。
新代码推荐直接从对应子模块导入。
"""

import logging
from repositories import get_user_settings, set_translation_mode
from repositories.chat_repo import search_messages
from stats import get_user_stats_text
from .base_handlers import check_permission_unified
from core.platform.models import UnifiedContext

# 从子模块导入

from .feature_handlers import (
    feature_command,
    handle_feature_input,
    save_feature_command,
)

logger = logging.getLogger(__name__)


# --- Stats (保留在此文件中，较小) ---


async def stats_command(ctx: UnifiedContext) -> None:
    """处理 /stats 命令"""
    if not await check_permission_unified(ctx):
        return

    user_id = ctx.message.user.id
    try:
        stats_text = await get_user_stats_text(user_id)
    except:
        stats_text = "Stats not available for non-numeric ID yet"

    await ctx.reply(stats_text)


# --- Translation (保留在此文件中，较小) ---


async def toggle_translation_command(ctx: UnifiedContext) -> None:
    """处理 /translate 命令，切换沉浸式翻译模式"""
    if not await check_permission_unified(ctx):
        return

    user_id = ctx.message.user.id  # Settings now support str IDs

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
        await ctx.reply("🚫 **沉浸式翻译模式：已关闭**\n\n已恢复正常 AI 助手模式。")


async def chatlog_command(ctx: UnifiedContext) -> None:
    """处理 /chatlog <keyword> 对话检索命令。"""
    if not await check_permission_unified(ctx):
        return

    text = str(ctx.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await ctx.reply("用法: `/chatlog <关键词>`")
        return

    keyword = parts[1].strip()
    user_id = str(ctx.message.user.id)
    rows = await search_messages(user_id=user_id, keyword=keyword, limit=10)
    if not rows:
        await ctx.reply("未找到匹配对话。")
        return

    lines = [f"🔎 对话检索：`{keyword}`（最近 {len(rows)} 条）"]
    for row in rows:
        lines.append(
            f"- `{row.get('created_at', '')}` | {row.get('role')} | {str(row.get('content') or '')[:120]}"
        )
    await ctx.reply("\n".join(lines))


# 导出所有函数
__all__ = [
    # Stats & Translation
    "stats_command",
    "toggle_translation_command",
    "chatlog_command",
    # Reminder
    # Feature
    "feature_command",
    "handle_feature_input",
    "save_feature_command",
]
