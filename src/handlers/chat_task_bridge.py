from __future__ import annotations

from core.platform.models import UnifiedContext
from core.session_task_store import session_task_store


async def maybe_bind_recent_followup_context(
    ctx: UnifiedContext,
    user_message: str,
) -> str:
    raw_message = str(user_message or "").strip()
    if not raw_message:
        return ""
    user_id = str(ctx.message.user.id)
    active_task = await session_task_store.get_active(user_id)
    if active_task is not None and active_task.status in {
        "planning",
        "running",
        "waiting_user",
    }:
        return ""
    matched = await session_task_store.match_followup(user_id, raw_message)
    if not isinstance(matched, dict):
        return ""
    return str(matched.get("context_text") or "").strip()
