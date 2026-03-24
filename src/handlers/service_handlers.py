"""
服务 handlers - 向后兼容层

重新导出各子模块中的函数，保持现有代码的兼容性。
新代码推荐直接从对应子模块导入。
"""

import logging
from core.skill_menu import cache_items, get_cached_items, make_callback, parse_callback
from core.state_store import get_recent_messages_for_user, search_messages
from .base_handlers import (
    check_permission_unified,
    edit_callback_message,
    get_effective_user_id,
)
from core.platform.models import UnifiedContext
from user_context import compact_current_session, get_context_length

# 从子模块导入

from .feature_handlers import (
    feature_command,
    handle_feature_input,
    save_feature_command,
)

logger = logging.getLogger(__name__)
CHATLOG_MENU_NS = "chatlog"
COMPACT_MENU_NS = "compact"


async def chatlog_command(ctx: UnifiedContext) -> None:
    """处理 /chatlog <keyword> 对话检索命令。"""
    if not await check_permission_unified(ctx):
        return

    text = str(ctx.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        user_id = get_effective_user_id(ctx)
        recent = await get_recent_messages_for_user(user_id=user_id, limit=5)
        lines = ["🔎 对话检索", "", "用法：`/chatlog <关键词>`", ""]
        if recent:
            lines.append("最近对话片段：")
            for row in recent[:5]:
                snippet = str(row.get("content") or "").strip().replace("\n", " ")
                lines.append(f"- {_truncate(snippet, 48)}")
        lines.append("")
        lines.append("提示：直接输入人名、项目名、主题词即可。")
        await ctx.reply(
            "\n".join(lines),
            ui={
                "actions": [
                    [
                        {"text": "示例：PR", "callback_data": make_callback(CHATLOG_MENU_NS, "hint", "PR")},
                        {"text": "示例：模型", "callback_data": make_callback(CHATLOG_MENU_NS, "hint", "模型")},
                    ]
                ]
            },
        )
        return

    keyword = parts[1].strip()
    user_id = get_effective_user_id(ctx)
    rows = await search_messages(user_id=user_id, keyword=keyword, limit=30)
    if not rows:
        await ctx.reply("未找到匹配对话。")
        return

    cache_items(ctx, CHATLOG_MENU_NS, "rows", rows)
    ctx.user_data["_chatlog_keyword"] = keyword
    payload, ui = _build_chatlog_page_payload(keyword, rows, page=0)
    await ctx.reply(payload, ui=ui)


async def compact_command(ctx: UnifiedContext) -> None:
    """处理 /compact，对当前会话执行手动压缩。"""
    if not await check_permission_unified(ctx):
        return

    text = str(ctx.message.text or "").strip()
    parts = text.split(maxsplit=1)
    action = str(parts[1]).strip().lower() if parts[1:] else ""
    if action in {"preview", "plan"}:
        try:
            dialog_count = await get_context_length(ctx, get_effective_user_id(ctx))
        except Exception:
            dialog_count = 0
        await ctx.reply(
            "🗜️ 会话压缩\n\n"
            f"当前上下文消息数：`{dialog_count}`\n\n"
            "确认后会把更早历史压成摘要，保留最近原始消息。",
            ui={
                "actions": [
                    [
                        {"text": "确认压缩", "callback_data": make_callback(COMPACT_MENU_NS, "run")},
                        {"text": "取消", "callback_data": make_callback(COMPACT_MENU_NS, "cancel")},
                    ]
                ]
            },
        )
        return

    user_id = get_effective_user_id(ctx)
    result = await compact_current_session(ctx, user_id, force=True)
    if not bool(result.get("ok")):
        await ctx.reply("⚠️ 当前会话压缩失败，请稍后重试。")
        return

    if not bool(result.get("compacted")):
        reason = str(result.get("reason") or "").strip().lower()
        if reason == "nothing_to_compact":
            await ctx.reply("ℹ️ 当前会话没有可压缩的更早历史。")
            return
        dialog_count = int(result.get("dialog_count") or 0)
        await ctx.reply(
            f"ℹ️ 当前会话共 {dialog_count} 条原始消息，暂未达到需要压缩的程度。"
        )
        return

    await ctx.reply(
        "🗜️ 已压缩 "
        f"{int(result.get('compressed_count') or 0)} 条历史，"
        "保留最近 "
        f"{int(result.get('kept_recent') or 0)} 条原始消息。"
    )


def _truncate(text: str, limit: int = 120) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _build_chatlog_page_payload(
    keyword: str,
    rows: list[dict],
    *,
    page: int = 0,
    prefix: str = "",
) -> tuple[str, dict]:
    page_size = 5
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    current_page = max(0, min(int(page or 0), total_pages - 1))
    start = current_page * page_size
    items = rows[start : start + page_size]

    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.append(f"🔎 对话检索：`{keyword}`（第 {current_page + 1}/{total_pages} 页）")
    for row in items:
        lines.append(
            f"- `{row.get('created_at', '')}` | {row.get('role')} | {_truncate(str(row.get('content') or ''), 120)}"
        )

    actions: list[list[dict[str, str]]] = []
    nav_row = []
    if current_page > 0:
        nav_row.append(
            {"text": "⬅️ 上一页", "callback_data": make_callback(CHATLOG_MENU_NS, "page", current_page - 1)}
        )
    if current_page < total_pages - 1:
        nav_row.append(
            {"text": "➡️ 下一页", "callback_data": make_callback(CHATLOG_MENU_NS, "page", current_page + 1)}
        )
    if nav_row:
        actions.append(nav_row)
    actions.append([{"text": "重新查看用法", "callback_data": make_callback(CHATLOG_MENU_NS, "home")}])
    return "\n".join(lines), {"actions": actions}


async def handle_chatlog_callback(ctx: UnifiedContext) -> None:
    data = ctx.callback_data
    if not data:
        return

    action, parts = parse_callback(data, CHATLOG_MENU_NS)
    if not action:
        return

    if action == "home":
        user_id = get_effective_user_id(ctx)
        recent = await get_recent_messages_for_user(user_id=user_id, limit=5)
        lines = ["🔎 对话检索", "", "用法：`/chatlog <关键词>`", ""]
        if recent:
            lines.append("最近对话片段：")
            for row in recent[:5]:
                lines.append(f"- {_truncate(str(row.get('content') or ''), 48)}")
        lines.append("")
        lines.append("提示：直接输入人名、项目名、主题词即可。")
        payload = "\n".join(lines)
        ui = {
            "actions": [
                [
                    {"text": "示例：PR", "callback_data": make_callback(CHATLOG_MENU_NS, "hint", "PR")},
                    {"text": "示例：模型", "callback_data": make_callback(CHATLOG_MENU_NS, "hint", "模型")},
                ]
            ]
        }
    elif action == "hint":
        keyword = str(parts[0] if parts else "").strip()
        payload = f"直接发送：`/chatlog {keyword}`"
        ui = {"actions": [[{"text": "返回", "callback_data": make_callback(CHATLOG_MENU_NS, "home")}]]}
    elif action == "page":
        rows = list(get_cached_items(ctx, CHATLOG_MENU_NS, "rows"))
        keyword = str(ctx.user_data.get("_chatlog_keyword") or "").strip() or "关键词"
        if not rows:
            payload = "❌ 检索结果已过期，请重新执行 `/chatlog <关键词>`。"
            ui = {"actions": [[{"text": "返回", "callback_data": make_callback(CHATLOG_MENU_NS, "home")}]]}
        else:
            page = int(str(parts[0] if parts else "0") or "0")
            payload, ui = _build_chatlog_page_payload(keyword, rows, page=page)
    else:
        payload = "❌ 未识别的检索菜单操作。"
        ui = {"actions": [[{"text": "返回", "callback_data": make_callback(CHATLOG_MENU_NS, "home")}]]}

    await edit_callback_message(ctx, payload, ui=ui)


async def handle_compact_callback(ctx: UnifiedContext) -> None:
    data = ctx.callback_data
    if not data:
        return

    action, _parts = parse_callback(data, COMPACT_MENU_NS)
    if not action:
        return

    if action == "cancel":
        await edit_callback_message(ctx, "已取消会话压缩。", no_change_text="已取消")
        return

    if action != "run":
        await edit_callback_message(ctx, "❌ 未识别的压缩操作。")
        return

    user_id = get_effective_user_id(ctx)
    result = await compact_current_session(ctx, user_id, force=True)
    if not bool(result.get("ok")):
        await edit_callback_message(ctx, "⚠️ 当前会话压缩失败，请稍后重试。")
        return

    if not bool(result.get("compacted")):
        reason = str(result.get("reason") or "").strip().lower()
        if reason == "nothing_to_compact":
            await edit_callback_message(ctx, "ℹ️ 当前会话没有可压缩的更早历史。")
            return
        dialog_count = int(result.get("dialog_count") or 0)
        await edit_callback_message(
            ctx,
            f"ℹ️ 当前会话共 {dialog_count} 条原始消息，暂未达到需要压缩的程度。",
        )
        return

    await edit_callback_message(
        ctx,
        "🗜️ 已压缩 "
        f"{int(result.get('compressed_count') or 0)} 条历史，"
        "保留最近 "
        f"{int(result.get('kept_recent') or 0)} 条原始消息。",
    )


# 导出所有函数
__all__ = [
    "chatlog_command",
    "compact_command",
    # Reminder
    # Feature
    "feature_command",
    "handle_feature_input",
    "save_feature_command",
]
