import logging
from core.platform.models import UnifiedContext, CONVERSATION_END
from core.config import is_user_allowed

logger = logging.getLogger(__name__)


async def check_permission_unified(context: UnifiedContext) -> bool:
    """Unified permission check"""

    user_id = context.message.user.id
    # Note: is_user_allowed now supports both int and str IDs
    # If we have string IDs (like DingTalk), this will work correctly.

    if context.callback_user_id:
        user_id = context.callback_user_id
    if not await is_user_allowed(user_id):
        await context.reply(
            f"⛔ 抱歉，您没有使用此 Bot 的权限。\n您的 ID 是: `{user_id}`"
        )
        return False
    return True


async def cancel(ctx: UnifiedContext) -> int:
    """取消当前操作"""
    await ctx.reply("操作已取消。\n\n发送消息继续 AI 对话，或使用 /download 下载视频。")
    return CONVERSATION_END
