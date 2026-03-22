from __future__ import annotations

from core.llm_usage_store import llm_usage_store
from core.platform.models import UnifiedContext

from .base_handlers import check_permission_unified


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


async def usage_command(ctx: UnifiedContext) -> None:
    if not await check_permission_unified(ctx):
        return

    text = getattr(ctx.message, "text", "") or ""
    sub, _args = _parse_subcommand(text)

    if sub in {"help", "h", "?"}:
        await ctx.reply(_usage_help_text())
        return

    if sub in {"today", "td"}:
        await ctx.reply(llm_usage_store.render_today_summary())
        return

    if sub in {"reset", "clear"}:
        removed = llm_usage_store.reset()
        await ctx.reply(f"已重置 LLM 用量统计，删除 `{removed}` 条记录。")
        return

    if sub not in {"show", "list", "ls"}:
        await ctx.reply(_usage_help_text())
        return

    await ctx.reply(llm_usage_store.render_summary())
