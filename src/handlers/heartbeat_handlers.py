import logging
import re

from core.heartbeat_store import heartbeat_store
from core.heartbeat_worker import heartbeat_worker
from core.platform.models import UnifiedContext
from core.skill_menu import make_callback, parse_callback
from .base_handlers import (
    check_permission_unified,
    edit_callback_message,
    require_feature_access,
)

logger = logging.getLogger(__name__)
HEARTBEAT_MENU_NS = "hbm"


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


def _format_delivery_target(target: dict[str, str] | None) -> str:
    platform = str((target or {}).get("platform") or "").strip()
    chat_id = str((target or {}).get("chat_id") or "").strip()
    if not platform or not chat_id:
        return "未设置"
    return f"{platform}:{chat_id}"


def _current_delivery_target(ctx: UnifiedContext) -> dict[str, str]:
    message = getattr(ctx, "message", None)
    chat = getattr(message, "chat", None)
    return {
        "platform": str(getattr(message, "platform", "") or "").strip(),
        "chat_id": str(getattr(chat, "id", "") or "").strip(),
    }


def _render_checklist(checklist_items: list[dict[str, object]]) -> str:
    if not checklist_items:
        return "（空）"
    lines: list[str] = []
    for item in checklist_items:
        index = int(item.get("index") or 0)
        text = str(item.get("text") or "").strip()
        target = item.get("delivery_target")
        lines.append(
            f"{index}. {text}\n   ↳ 推送渠道: `{_format_delivery_target(target if isinstance(target, dict) else {})}`"
        )
    return "\n".join(lines)


def _split_reply_chunks(text: str, limit: int = 3500) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if len(raw) <= limit:
        return [raw]

    chunks: list[str] = []
    remaining = raw
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        cut = remaining.rfind("\n\n", 0, limit)
        if cut < int(limit * 0.6):
            cut = remaining.rfind("\n", 0, limit)
        if cut < int(limit * 0.4):
            cut = limit

        part = remaining[:cut].strip()
        if part:
            chunks.append(part)
        remaining = remaining[cut:].strip()

    return chunks


def _heartbeat_usage_text() -> str:
    return (
        "用法:\n"
        "`/heartbeat list`\n"
        "`/heartbeat add <item>`\n"
        "`/heartbeat remove <index>`\n"
        "`/heartbeat pause`\n"
        "`/heartbeat resume`\n"
        "`/heartbeat run`\n"
        "`/heartbeat every <30m|1h|600s>`\n"
        "`/heartbeat hours <08:00-22:00>`\n"
        "`/heartbeat help`"
    )


def _heartbeat_menu_ui(
    *,
    paused: bool,
    checklist_items: list[dict[str, object]],
    allow_delete: bool = True,
) -> dict:
    checklist_count = len(checklist_items)
    actions = [
        [
            {"text": "🔄 运行一次", "callback_data": make_callback(HEARTBEAT_MENU_NS, "run")},
            {
                "text": "▶️ 恢复" if paused else "⏸️ 暂停",
                "callback_data": make_callback(
                    HEARTBEAT_MENU_NS,
                    "resume" if paused else "pause",
                ),
            },
        ],
        [
            {"text": "⏱️ 30m", "callback_data": make_callback(HEARTBEAT_MENU_NS, "every", "30m")},
            {"text": "⏱️ 1h", "callback_data": make_callback(HEARTBEAT_MENU_NS, "every", "1h")},
        ],
        [
            {
                "text": "🕘 09:00-21:00",
                "callback_data": make_callback(HEARTBEAT_MENU_NS, "hours", "09:00-21:00"),
            },
            {
                "text": "🌙 08:00-22:00",
                "callback_data": make_callback(HEARTBEAT_MENU_NS, "hours", "08:00-22:00"),
            },
        ],
    ]
    if allow_delete and checklist_count > 0:
        delete_row = []
        route_row = []
        for index in range(min(4, checklist_count)):
            delete_row.append(
                {
                    "text": f"❌ 删除 {index + 1}",
                    "callback_data": make_callback(HEARTBEAT_MENU_NS, "del", index + 1),
                }
            )
            route_row.append(
                {
                    "text": f"📍 渠道 {index + 1}",
                    "callback_data": make_callback(HEARTBEAT_MENU_NS, "route", index + 1),
                }
            )
        actions.append(delete_row)
        actions.append(route_row)
    actions.append([{"text": "ℹ️ 帮助", "callback_data": make_callback(HEARTBEAT_MENU_NS, "help")}])
    return {"actions": actions}


async def _build_heartbeat_payload(
    user_id: str,
    *,
    prefix: str = "",
) -> tuple[str, dict]:
    state = await heartbeat_store.get_state(user_id)
    spec = dict(state.get("spec") or {})
    status = dict(state.get("status") or {})
    hb_status = dict(status.get("heartbeat") or {})
    checklist_items = await heartbeat_store.list_checklist_items(user_id)
    delivery = dict(status.get("delivery") or {})

    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.extend(
        [
            "💓 Heartbeat 配置",
            "",
            f"- every: `{spec.get('every')}`",
            f"- target: `{spec.get('target')}`",
            f"- active_hours: `{(spec.get('active_hours') or {}).get('start')}`-`{(spec.get('active_hours') or {}).get('end')}`",
            f"- paused: `{spec.get('paused')}`",
            f"- fallback_channel: `{_format_delivery_target({'platform': str(delivery.get('last_platform') or ''), 'chat_id': str(delivery.get('last_chat_id') or '')})}`",
            "",
            f"- last_level: `{hb_status.get('last_level', 'OK')}`",
            f"- last_run_at: `{hb_status.get('last_run_at', '')}`",
            "",
            "Checklist:",
            _render_checklist(checklist_items),
            "",
            "也支持直接输入：`/heartbeat add <检查项>`、`/heartbeat remove <序号>`。",
            "在对应聊天里点「📍 渠道 N」即可把该检查项的推送渠道改成当前聊天。",
        ]
    )
    return "\n".join(lines), _heartbeat_menu_ui(
        paused=bool(spec.get("paused")),
        checklist_items=checklist_items,
    )


async def heartbeat_command(ctx: UnifiedContext) -> None:
    """管理心跳清单与策略: /heartbeat [list|add|remove|pause|resume|run|every|hours]"""
    if not await check_permission_unified(ctx):
        return
    if not await require_feature_access(ctx, "heartbeat"):
        return

    user_id = str(ctx.message.user.id)
    text = ctx.message.text or ""
    sub, args = _parse_subcommand(text)

    if sub in {"help", "h", "?"}:
        await ctx.reply(_heartbeat_usage_text(), ui=_heartbeat_menu_ui(paused=False, checklist_items=[], allow_delete=False))
        return

    if sub in {"list", "ls", "show"}:
        payload, ui = await _build_heartbeat_payload(user_id)
        await ctx.reply(payload, ui=ui)
        return

    if sub == "add":
        item = args.strip()
        if not item:
            await ctx.reply("用法: `/heartbeat add <检查项>`")
            return
        current_target = _current_delivery_target(ctx)
        checklist = await heartbeat_store.add_checklist_item(
            user_id,
            item,
            platform=current_target["platform"],
            chat_id=current_target["chat_id"],
        )
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix=f"✅ 已添加。当前共 {len(checklist)} 项。",
        )
        await ctx.reply(payload, ui=ui)
        return

    if sub in {"remove", "rm", "del", "delete"}:
        try:
            index = int(args.strip())
        except Exception:
            await ctx.reply("用法: `/heartbeat remove <序号>`")
            return
        checklist = await heartbeat_store.remove_checklist_item(user_id, index)
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix=f"✅ 已更新。当前共 {len(checklist)} 项。",
        )
        await ctx.reply(payload, ui=ui)
        return

    if sub == "pause":
        await heartbeat_store.set_heartbeat_spec(user_id, paused=True)
        payload, ui = await _build_heartbeat_payload(user_id, prefix="⏸️ Heartbeat 已暂停。")
        await ctx.reply(payload, ui=ui)
        return

    if sub == "resume":
        await heartbeat_store.set_heartbeat_spec(user_id, paused=False)
        payload, ui = await _build_heartbeat_payload(user_id, prefix="▶️ Heartbeat 已恢复。")
        await ctx.reply(payload, ui=ui)
        return

    if sub == "run":
        await ctx.reply("🔄 正在手动执行一次 Heartbeat...")
        result = await heartbeat_worker.run_user_now(user_id, suppress_push=True)
        state = await heartbeat_store.get_state(user_id)
        level = str(
            ((state.get("status") or {}).get("heartbeat") or {}).get(
                "last_level", "NOTICE"
            )
        )
        full_text = f"✅ Heartbeat 运行完成（level={level}）：\n{result}"
        chunks = _split_reply_chunks(full_text)
        if not chunks:
            await ctx.reply("✅ Heartbeat 运行完成。")
            return
        total = len(chunks)
        for idx, chunk in enumerate(chunks, start=1):
            payload = chunk
            if total > 1:
                payload = f"[{idx}/{total}]\n{chunk}"
            await ctx.reply(payload)
        return

    if sub == "every":
        value = args.strip().lower()
        if not re.fullmatch(r"\d+\s*[smhd]?", value):
            await ctx.reply("用法: `/heartbeat every <30m|1h|600s>`")
            return
        spec = await heartbeat_store.set_heartbeat_spec(user_id, every=value)
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix=f"✅ every 已设置为 `{spec.get('every')}`",
        )
        await ctx.reply(payload, ui=ui)
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
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix=f"✅ active_hours 已设置为 `{start}-{end}`",
        )
        await ctx.reply(payload, ui=ui)
        return

    await ctx.reply(_heartbeat_usage_text(), ui=_heartbeat_menu_ui(paused=False, checklist_items=[], allow_delete=False))


async def handle_heartbeat_callback(ctx: UnifiedContext) -> None:
    if not await require_feature_access(ctx, "heartbeat"):
        return
    data = ctx.callback_data
    if not data:
        return

    action, parts = parse_callback(data, HEARTBEAT_MENU_NS)
    if not action:
        return

    user_id = str(ctx.callback_user_id or ctx.message.user.id)

    if action == "run":
        result = await heartbeat_worker.run_user_now(user_id, suppress_push=True)
        state = await heartbeat_store.get_state(user_id)
        level = str(
            ((state.get("status") or {}).get("heartbeat") or {}).get(
                "last_level", "NOTICE"
            )
        )
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix=f"✅ Heartbeat 运行完成（level={level}）：\n{result}",
        )
    elif action == "pause":
        await heartbeat_store.set_heartbeat_spec(user_id, paused=True)
        payload, ui = await _build_heartbeat_payload(user_id, prefix="⏸️ Heartbeat 已暂停。")
    elif action == "resume":
        await heartbeat_store.set_heartbeat_spec(user_id, paused=False)
        payload, ui = await _build_heartbeat_payload(user_id, prefix="▶️ Heartbeat 已恢复。")
    elif action == "every":
        every = str(parts[0] if parts else "").strip().lower()
        spec = await heartbeat_store.set_heartbeat_spec(user_id, every=every)
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix=f"✅ every 已设置为 `{spec.get('every')}`",
        )
    elif action == "hours":
        value = str(parts[0] if parts else "").strip()
        match = re.fullmatch(r"(\d{2}:\d{2})-(\d{2}:\d{2})", value)
        if not match:
            payload, ui = await _build_heartbeat_payload(
                user_id,
                prefix="❌ 时间段参数无效，请重新选择。",
            )
        else:
            start, end = match.group(1), match.group(2)
            await heartbeat_store.set_heartbeat_spec(
                user_id,
                active_start=start,
                active_end=end,
            )
            payload, ui = await _build_heartbeat_payload(
                user_id,
                prefix=f"✅ active_hours 已设置为 `{start}-{end}`",
            )
    elif action == "del":
        try:
            index = int(str(parts[0] if parts else "").strip())
        except Exception:
            index = 0
        checklist = await heartbeat_store.remove_checklist_item(user_id, index)
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix=f"✅ 已更新。当前共 {len(checklist)} 项。",
        )
    elif action == "route":
        try:
            index = int(str(parts[0] if parts else "").strip())
        except Exception:
            index = 0
        current_target = _current_delivery_target(ctx)
        changed = await heartbeat_store.set_checklist_item_delivery(
            user_id,
            index,
            current_target["platform"],
            current_target["chat_id"],
        )
        if changed is None:
            payload, ui = await _build_heartbeat_payload(
                user_id,
                prefix="❌ 检查项不存在，无法更新推送渠道。",
            )
        else:
            payload, ui = await _build_heartbeat_payload(
                user_id,
                prefix=(
                    f"✅ 已把检查项 {index} 的推送渠道设置为当前聊天 "
                    f"`{_format_delivery_target(changed.get('delivery_target') if isinstance(changed, dict) else {})}`。"
                ),
            )
    elif action == "help":
        state = await heartbeat_store.get_state(user_id)
        checklist_items = await heartbeat_store.list_checklist_items(user_id)
        spec = dict(state.get("spec") or {})
        payload = _heartbeat_usage_text()
        ui = _heartbeat_menu_ui(
            paused=bool(spec.get("paused")),
            checklist_items=checklist_items,
            allow_delete=False,
        )
    else:
        payload, ui = await _build_heartbeat_payload(
            user_id,
            prefix="❌ 未识别的 Heartbeat 操作。",
        )

    await edit_callback_message(ctx, payload, ui=ui)
