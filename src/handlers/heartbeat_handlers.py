import logging
import re

from core.heartbeat_store import heartbeat_store
from core.heartbeat_worker import heartbeat_worker
from core.platform.models import UnifiedContext
from .base_handlers import check_permission_unified

logger = logging.getLogger(__name__)


def _parse_subcommand(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "list", ""
    parts = raw.split(maxsplit=2)
    if not parts:
        return "list", ""
    if not parts[0].startswith("/heartbeat"):
        return "list", ""
    if len(parts) == 1:
        return "list", ""
    cmd = parts[1].strip().lower()
    args = parts[2].strip() if len(parts) >= 3 else ""
    return cmd, args


def _render_checklist(checklist: list[str]) -> str:
    if not checklist:
        return "（空）"
    return "\n".join([f"{idx}. {item}" for idx, item in enumerate(checklist, start=1)])


async def heartbeat_command(ctx: UnifiedContext) -> None:
    """管理心跳清单与策略: /heartbeat [list|add|remove|pause|resume|run|every|hours]"""
    if not await check_permission_unified(ctx):
        return

    user_id = str(ctx.message.user.id)
    text = ctx.message.text or ""
    sub, args = _parse_subcommand(text)

    if sub in {"list", "ls", "show"}:
        state = await heartbeat_store.get_state(user_id)
        spec = dict(state.get("spec") or {})
        status = dict(state.get("status") or {})
        hb_status = dict(status.get("heartbeat") or {})
        checklist = list(state.get("checklist") or [])
        await ctx.reply(
            "💓 Heartbeat 配置\n\n"
            f"- every: `{spec.get('every')}`\n"
            f"- target: `{spec.get('target')}`\n"
            f"- active_hours: `{(spec.get('active_hours') or {}).get('start')}`"
            f"-`{(spec.get('active_hours') or {}).get('end')}`\n"
            f"- paused: `{spec.get('paused')}`\n\n"
            f"- last_level: `{hb_status.get('last_level', 'OK')}`\n"
            f"- last_run_at: `{hb_status.get('last_run_at', '')}`\n\n"
            "Checklist:\n"
            f"{_render_checklist(checklist)}"
        )
        return

    if sub == "add":
        item = args.strip()
        if not item:
            await ctx.reply("用法: `/heartbeat add <检查项>`")
            return
        checklist = await heartbeat_store.add_checklist_item(user_id, item)
        await ctx.reply(f"✅ 已添加。当前共 {len(checklist)} 项。")
        return

    if sub in {"remove", "rm", "del", "delete"}:
        try:
            index = int(args.strip())
        except Exception:
            await ctx.reply("用法: `/heartbeat remove <序号>`")
            return
        checklist = await heartbeat_store.remove_checklist_item(user_id, index)
        await ctx.reply(f"✅ 已更新。当前共 {len(checklist)} 项。")
        return

    if sub == "pause":
        await heartbeat_store.set_heartbeat_spec(user_id, paused=True)
        await ctx.reply("⏸️ Heartbeat 已暂停。")
        return

    if sub == "resume":
        await heartbeat_store.set_heartbeat_spec(user_id, paused=False)
        await ctx.reply("▶️ Heartbeat 已恢复。")
        return

    if sub == "run":
        await ctx.reply("🔄 正在手动执行一次 Heartbeat...")
        result = await heartbeat_worker.run_user_now(user_id)
        state = await heartbeat_store.get_state(user_id)
        level = str(((state.get("status") or {}).get("heartbeat") or {}).get("last_level", "NOTICE"))
        await ctx.reply(f"✅ Heartbeat 运行完成（level={level}）：\n{result}")
        return

    if sub == "every":
        value = args.strip().lower()
        if not re.fullmatch(r"\d+\s*[smhd]?", value):
            await ctx.reply("用法: `/heartbeat every <30m|1h|600s>`")
            return
        spec = await heartbeat_store.set_heartbeat_spec(user_id, every=value)
        await ctx.reply(f"✅ every 已设置为 `{spec.get('every')}`")
        return

    if sub == "hours":
        value = args.strip()
        match = re.fullmatch(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", value)
        if not match:
            await ctx.reply("用法: `/heartbeat hours <08:00-22:00>`")
            return
        start, end = match.group(1), match.group(2)
        await heartbeat_store.set_heartbeat_spec(
            user_id, active_start=start, active_end=end
        )
        await ctx.reply(f"✅ active_hours 已设置为 `{start}-{end}`")
        return

    await ctx.reply(
        "用法:\n"
        "`/heartbeat list`\n"
        "`/heartbeat add <item>`\n"
        "`/heartbeat remove <index>`\n"
        "`/heartbeat pause`\n"
        "`/heartbeat resume`\n"
        "`/heartbeat run`\n"
        "`/heartbeat every <30m|1h|600s>`\n"
        "`/heartbeat hours <08:00-22:00>`"
    )
