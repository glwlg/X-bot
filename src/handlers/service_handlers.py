"""
服务 handlers - 向后兼容层

重新导出各子模块中的函数，保持现有代码的兼容性。
新代码推荐直接从对应子模块导入。
"""

import logging
from core.state_store import search_messages
from .base_handlers import check_permission_unified
from core.platform.models import UnifiedContext

# 从子模块导入

from .feature_handlers import (
    feature_command,
    handle_feature_input,
    save_feature_command,
)

logger = logging.getLogger(__name__)


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
    "chatlog_command",
    # Reminder
    # Feature
    "feature_command",
    "handle_feature_input",
    "save_feature_command",
]
