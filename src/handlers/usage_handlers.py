from __future__ import annotations

from core.llm_usage_store import llm_usage_store
from core.platform.models import UnifiedContext
from core.skill_menu import make_callback, parse_callback

from .base_handlers import check_permission_unified, edit_callback_message

USAGE_MENU_NS = "usagem"


def _parse_subcommand(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "show", ""
    parts = raw.split(maxsplit=2)
    if not parts:
        return "show", ""
    if not parts[0].startswith("/usage"):
        return "show", ""
    if len(parts) == 1:
        return "show", ""
    cmd = parts[1].strip().lower()
    args = parts[2].strip() if len(parts) >= 3 else ""
    return cmd, args


def _usage_help_text() -> str:
    return (
        "用法:\n"
        "`/usage`\n"
        "`/usage show`\n"
        "`/usage today`\n"
        "`/usage reset`\n"
        "`/usage help`\n\n"
        "说明：展示按模型聚合的 LLM 调用次数、输入/输出 token、总 token、缓存命中请求数和缓存命中 token。"
    )


def _usage_menu_ui(*, confirm_reset: bool = False) -> dict:
    if confirm_reset:
        return {
            "actions": [
                [
                    {"text": "🗑️ 确认重置", "callback_data": make_callback(USAGE_MENU_NS, "resetconfirm")},
                    {"text": "↩️ 返回", "callback_data": make_callback(USAGE_MENU_NS, "show")},
                ]
            ]
        }

    return {
        "actions": [
            [
                {"text": "📊 总览", "callback_data": make_callback(USAGE_MENU_NS, "show")},
                {"text": "📅 今日", "callback_data": make_callback(USAGE_MENU_NS, "today")},
            ],
            [
                {"text": "🗑️ 重置统计", "callback_data": make_callback(USAGE_MENU_NS, "reset")},
            ],
        ]
    }


def _build_usage_payload(mode: str = "show", *, prefix: str = "") -> tuple[str, dict]:
    normalized = str(mode or "show").strip().lower()
    if normalized == "today":
        body = llm_usage_store.render_today_summary()
    elif normalized == "reset":
        body = (
            "⚠️ 确认重置 LLM 用量统计？\n\n"
            "这会清空当前数据库中的聚合记录。"
        )
        return body, _usage_menu_ui(confirm_reset=True)
    elif normalized == "help":
        body = _usage_help_text()
    else:
        body = llm_usage_store.render_summary()

    if prefix:
        body = f"{prefix.strip()}\n\n{body}"
    return body, _usage_menu_ui()


async def usage_command(ctx: UnifiedContext) -> None:
    if not await check_permission_unified(ctx):
        return

    text = getattr(ctx.message, "text", "") or ""
    sub, args = _parse_subcommand(text)

    if sub in {"help", "h", "?"}:
        payload, ui = _build_usage_payload("help")
        await ctx.reply(payload, ui=ui)
        return

    if sub in {"today", "td"}:
        payload, ui = _build_usage_payload("today")
        await ctx.reply(payload, ui=ui)
        return

    if sub in {"reset", "clear"}:
        removed = llm_usage_store.reset()
        payload, ui = _build_usage_payload(
            "show",
            prefix=f"已重置 LLM 用量统计，删除 `{removed}` 条记录。",
        )
        await ctx.reply(payload, ui=ui)
        return

    if sub not in {"show", "list", "ls"}:
        payload, ui = _build_usage_payload("help")
        await ctx.reply(payload, ui=ui)
        return

    payload, ui = _build_usage_payload("show")
    await ctx.reply(payload, ui=ui)


async def handle_usage_callback(ctx: UnifiedContext) -> None:
    data = ctx.callback_data
    if not data:
        return

    action, _parts = parse_callback(data, USAGE_MENU_NS)
    if not action:
        return

    if action == "show":
        payload, ui = _build_usage_payload("show")
    elif action == "today":
        payload, ui = _build_usage_payload("today")
    elif action == "reset":
        payload, ui = _build_usage_payload("reset")
    elif action == "resetconfirm":
        removed = llm_usage_store.reset()
        payload, ui = _build_usage_payload(
            "show",
            prefix=f"已重置 LLM 用量统计，删除 `{removed}` 条记录。",
        )
    else:
        payload, ui = _build_usage_payload("help")

    await edit_callback_message(ctx, payload, ui=ui)
