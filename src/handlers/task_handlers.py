from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from core.channel_runtime_store import channel_runtime_store
from core.heartbeat_store import heartbeat_store
from core.platform.models import UnifiedContext
from core.skill_menu import (
    cache_items,
    get_cached_item,
    make_callback,
    menu_store,
    parse_callback,
)
from core.task_inbox import task_inbox

from .base_handlers import (
    check_permission_unified,
    edit_callback_message,
    get_effective_user_id,
)

TASK_MENU_NS = "taskm"
logger = logging.getLogger(__name__)
_TASK_ACTION_REFS_KEY = "__task_action_refs__"


def _parse_subcommand(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "recent", ""
    parts = raw.split(maxsplit=2)
    if not parts or not parts[0].startswith("/task"):
        return "recent", ""
    if len(parts) == 1:
        return "recent", ""
    cmd = parts[1].strip().lower()
    args = parts[2].strip() if len(parts) >= 3 else ""
    return cmd, args


def _compact(text: Any, limit: int = 48) -> str:
    raw = str(text or "").strip().replace("\n", " ")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _should_show_task(item: Any) -> bool:
    source = str(getattr(item, "source", "") or "").strip().lower()
    if source != "heartbeat":
        return True
    status = str(getattr(item, "status", "") or "").strip().lower()
    return status == "waiting_external"


def _task_usage_text() -> str:
    return "用法: `/task`、`/task recent` 或 `/task open`"


def _cache_task_action_ref(
    ctx: UnifiedContext,
    *,
    view: str,
    index: str | int | None,
    task_id: str,
) -> str:
    token = uuid4().hex[:12]
    store = menu_store(ctx, TASK_MENU_NS)
    refs = store.setdefault(_TASK_ACTION_REFS_KEY, {})
    if not isinstance(refs, dict):
        refs = {}
        store[_TASK_ACTION_REFS_KEY] = refs
    refs[token] = {
        "view": str(view or "").strip() or "recent",
        "index": str(index if index is not None else "").strip(),
        "task_id": str(task_id or "").strip(),
    }
    if len(refs) > 128:
        stale_tokens = list(refs.keys())[:-64]
        for stale_token in stale_tokens:
            refs.pop(stale_token, None)
    return token


def _resolve_task_action_ref(
    ctx: UnifiedContext,
    token: str | None,
) -> tuple[str, str, str]:
    raw_token = str(token or "").strip()
    if not raw_token:
        return "", "", ""
    store = menu_store(ctx, TASK_MENU_NS)
    refs = store.get(_TASK_ACTION_REFS_KEY, {})
    if not isinstance(refs, dict):
        return "", "", ""
    payload = refs.get(raw_token)
    if not isinstance(payload, dict):
        return "", "", ""
    return (
        str(payload.get("view") or "").strip(),
        str(payload.get("index") or "").strip(),
        str(payload.get("task_id") or "").strip(),
    )


async def _active_confirmation_row(user_id: str) -> list[dict[str, str]]:
    active_task = channel_runtime_store.get_active_task(platform_user_id=user_id)
    if not active_task:
        active_task = await heartbeat_store.get_session_active_task(user_id)
    if not active_task or str(active_task.get("status") or "") != "waiting_user":
        return []
    return [
        {"text": "继续当前任务", "callback_data": "task_continue"},
        {"text": "停止当前任务", "callback_data": "task_stop"},
    ]


async def _build_task_list_payload(
    ctx: UnifiedContext,
    *,
    view: str = "recent",
    page: int = 0,
    prefix: str = "",
) -> tuple[str, dict]:
    user_id = get_effective_user_id(ctx)
    normalized_view = "open" if str(view or "").strip().lower() == "open" else "recent"
    if normalized_view == "open":
        rows = await task_inbox.list_open(user_id=user_id, limit=30)
        title = "🧾 未完成任务"
    else:
        rows = await task_inbox.list_recent(user_id=user_id, limit=30)
        title = "🧾 最近 10 个任务"
    rows = [row for row in rows if _should_show_task(row)]
    cache_items(ctx, TASK_MENU_NS, normalized_view, rows)

    actions: list[list[dict[str, str]]] = []
    confirm_row = await _active_confirmation_row(user_id)
    if confirm_row:
        actions.append(confirm_row)

    if not rows:
        if prefix:
            title = f"{prefix}\n\n{title}"
        actions.append(
            [
                {"text": "最近任务", "callback_data": make_callback(TASK_MENU_NS, "recent", 0)},
                {"text": "未完成任务", "callback_data": make_callback(TASK_MENU_NS, "open", 0)},
            ]
        )
        return f"{title}\n\n当前没有 manager 任务记录。", {"actions": actions}

    page_size = 10
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    current_page = max(0, min(int(page or 0), total_pages - 1))
    start = current_page * page_size
    items = rows[start : start + page_size]

    lines: list[str] = []
    if prefix:
        lines.extend([prefix.strip(), ""])
    lines.extend([f"{title}（第 {current_page + 1}/{total_pages} 页）"])
    for absolute_index, item in enumerate(items, start=start):
        lines.append(
            f"- `{item.task_id}` | {item.status} | {item.source} | {_compact(item.goal, 36)}"
        )
        metadata = dict(item.metadata or {}) if isinstance(item.metadata, dict) else {}
        followup = dict(metadata.get("followup") or {}) if isinstance(metadata.get("followup"), dict) else {}
        refs = dict(followup.get("refs") or {}) if isinstance(followup.get("refs"), dict) else {}
        done_when = str(followup.get("done_when") or "").strip()
        if done_when:
            lines.append(f"  done_when: {done_when}")
        pr_url = str(refs.get("pr_url") or "").strip()
        if pr_url:
            lines.append(f"  pr: {pr_url}")

    for absolute_index, item in enumerate(items, start=start):
        actions.append(
            [
                {
                    "text": f"{item.status} | {_compact(item.goal, 18)}",
                    "callback_data": make_callback(TASK_MENU_NS, "show", normalized_view, absolute_index),
                }
            ]
        )

    nav_row = []
    if current_page > 0:
        nav_row.append(
            {"text": "⬅️ 上一页", "callback_data": make_callback(TASK_MENU_NS, normalized_view, current_page - 1)}
        )
    if current_page < total_pages - 1:
        nav_row.append(
            {"text": "➡️ 下一页", "callback_data": make_callback(TASK_MENU_NS, normalized_view, current_page + 1)}
        )
    if nav_row:
        actions.append(nav_row)
    actions.append(
        [
            {"text": "最近任务", "callback_data": make_callback(TASK_MENU_NS, "recent", 0)},
            {"text": "未完成任务", "callback_data": make_callback(TASK_MENU_NS, "open", 0)},
        ]
    )
    return "\n".join(lines), {"actions": actions}


async def _build_task_detail_payload(
    ctx: UnifiedContext,
    *,
    view: str,
    index: str | int | None,
) -> tuple[str, dict]:
    item = get_cached_item(ctx, TASK_MENU_NS, view, index)
    if item is None:
        return await _build_task_list_payload(
            ctx,
            view=view,
            prefix="❌ 任务列表已过期，请重新选择。",
        )

    metadata = dict(item.metadata or {}) if isinstance(item.metadata, dict) else {}
    followup = metadata.get("followup")
    followup_obj = dict(followup) if isinstance(followup, dict) else {}
    refs = dict(followup_obj.get("refs") or {}) if isinstance(followup_obj.get("refs"), dict) else {}
    lines = [
        f"🧾 任务详情：`{item.task_id}`",
        "",
        f"- 状态：`{item.status}`",
        f"- 来源：`{item.source}`",
        f"- 更新时间：`{item.updated_at}`",
        f"- 目标：{str(item.goal or '').strip()}",
    ]
    if followup_obj:
        lines.append(f"- Follow-up：{str(followup_obj.get('done_when') or '').strip()}")
    if refs.get("pr_url"):
        lines.append(f"- PR：{refs.get('pr_url')}")
    if getattr(item, "result", None):
        lines.append(f"- Result：{_compact(getattr(item, 'result'), 80)}")
    if getattr(item, "output", None):
        lines.append(f"- Output：{_compact(getattr(item, 'output'), 80)}")

    delete_token = _cache_task_action_ref(
        ctx,
        view=view,
        index=index,
        task_id=item.task_id,
    )

    return "\n".join(lines), {
        "actions": [
            [
                {
                    "text": "🗑️ 删除任务",
                    "callback_data": make_callback(TASK_MENU_NS, "delete", delete_token),
                },
                {"text": "返回列表", "callback_data": make_callback(TASK_MENU_NS, view, 0)},
            ]
        ]
    }


async def _build_task_delete_confirm_payload(
    ctx: UnifiedContext,
    *,
    view: str,
    index: str | int | None,
    task_id: str,
) -> tuple[str, dict]:
    item = get_cached_item(ctx, TASK_MENU_NS, view, index)
    if item is None and str(task_id or "").strip():
        item = await task_inbox.get(str(task_id or "").strip())
    if item is None:
        return await _build_task_list_payload(
            ctx,
            view=view,
            prefix="❌ 任务不存在或已删除。",
        )

    lines = [
        f"⚠️ 确认删除任务：`{item.task_id}`",
        "",
        f"- 状态：`{item.status}`",
        f"- 来源：`{item.source}`",
        f"- 目标：{str(item.goal or '').strip()}",
        "",
        "删除后这条任务会从任务列表中移除；如果它正处于会话活跃状态，也会一并清理。",
    ]
    confirm_token = _cache_task_action_ref(
        ctx,
        view=view,
        index=index,
        task_id=item.task_id,
    )

    return "\n".join(lines), {
        "actions": [
            [
                {
                    "text": "确认删除",
                    "callback_data": make_callback(
                        TASK_MENU_NS,
                        "deleteconfirm",
                        confirm_token,
                    ),
                },
                {
                    "text": "取消",
                    "callback_data": make_callback(TASK_MENU_NS, "show", view, index),
                },
            ],
            [
                {"text": "返回列表", "callback_data": make_callback(TASK_MENU_NS, view, 0)},
            ],
        ]
    }


async def _delete_task_from_menu(
    ctx: UnifiedContext,
    *,
    task_id: str,
) -> tuple[bool, str]:
    safe_task_id = str(task_id or "").strip()
    if not safe_task_id:
        return False, "❌ 缺少任务 ID。"

    user_id = get_effective_user_id(ctx)
    item = await task_inbox.get(safe_task_id)
    if item is None or str(item.user_id or "").strip() != user_id:
        return False, "❌ 任务不存在或已删除。"

    cleared_active = False
    try:
        active = channel_runtime_store.get_active_task(platform_user_id=user_id)
        if not active:
            active = await heartbeat_store.get_session_active_task(user_id)
        active_ids = {
            str((active or {}).get("id") or "").strip(),
            str((active or {}).get("task_inbox_id") or "").strip(),
            str((active or {}).get("session_task_id") or "").strip(),
        }
        active_ids.discard("")
        if safe_task_id in active_ids:
            channel_runtime_store.update_active_task(
                platform_user_id=user_id,
                status="cancelled",
                result_summary="Deleted from /task menu.",
                needs_confirmation=False,
                confirmation_deadline="",
                clear_active=True,
            )
            await heartbeat_store.update_session_active_task(
                user_id,
                status="cancelled",
                result_summary="Deleted from /task menu.",
                needs_confirmation=False,
                confirmation_deadline="",
                clear_active=True,
            )
            await heartbeat_store.append_session_event(
                user_id,
                f"user_deleted_task:{safe_task_id}",
            )
            cleared_active = True
    except Exception:
        logger.warning("failed to clear heartbeat active task for %s", safe_task_id, exc_info=True)

    cancelled_runtime = False
    try:
        from core.task_manager import task_manager

        active_info = task_manager.get_task_info(user_id) or {}
        tracked_ids = {
            str(active_info.get("active_task_id") or "").strip(),
            str(active_info.get("task_id") or "").strip(),
        }
        tracked_ids.discard("")
        if safe_task_id in tracked_ids:
            await task_manager.cancel_task(user_id)
            cancelled_runtime = True
            try:
                from core.subagent_supervisor import subagent_supervisor

                await subagent_supervisor.cancel_for_user(
                    user_id=user_id,
                    reason="deleted_from_task_menu",
                )
            except Exception:
                logger.warning("failed to cancel subagents for %s", safe_task_id, exc_info=True)
    except Exception:
        logger.warning("failed to cancel runtime task for %s", safe_task_id, exc_info=True)

    user_data = getattr(ctx, "user_data", None)
    if isinstance(user_data, dict) and str(user_data.get("task_inbox_id") or "").strip() == safe_task_id:
        user_data.pop("task_inbox_id", None)

    deleted = await task_inbox.delete(safe_task_id)
    if not deleted:
        return False, "❌ 任务不存在或已删除。"

    suffix = ""
    if cleared_active or cancelled_runtime:
        suffix = " 已同步清理活跃任务状态。"
    return True, f"已删除任务 `{safe_task_id}`。{suffix}".strip()


async def task_command(ctx: UnifiedContext) -> None:
    if not await check_permission_unified(ctx):
        return

    text = getattr(ctx.message, "text", "") or ""
    sub, _args = _parse_subcommand(text)
    if sub not in {"recent", "list", "ls", "open"}:
        await ctx.reply(_task_usage_text())
        return

    normalized_view = "open" if sub == "open" else "recent"
    payload, ui = await _build_task_list_payload(ctx, view=normalized_view)
    await ctx.reply(payload, ui=ui)


async def handle_task_callback(ctx: UnifiedContext) -> None:
    data = ctx.callback_data
    if not data:
        return

    action, parts = parse_callback(data, TASK_MENU_NS)
    if not action:
        return

    if action in {"recent", "open"}:
        page = int(str(parts[0] if parts else "0") or "0")
        payload, ui = await _build_task_list_payload(ctx, view=action, page=page)
    elif action == "show":
        view = str(parts[0] if parts else "recent").strip() or "recent"
        index = parts[1] if len(parts) >= 2 else ""
        payload, ui = await _build_task_detail_payload(ctx, view=view, index=index)
    elif action == "delete":
        if len(parts) == 1:
            view, index, task_id = _resolve_task_action_ref(ctx, parts[0])
        else:
            view = str(parts[0] if parts else "recent").strip() or "recent"
            index = parts[1] if len(parts) >= 2 else ""
            task_id = str(parts[2] if len(parts) >= 3 else "").strip()
        payload, ui = await _build_task_delete_confirm_payload(
            ctx,
            view=view or "recent",
            index=index,
            task_id=task_id,
        )
    elif action == "deleteconfirm":
        if len(parts) == 1:
            view, _index, task_id = _resolve_task_action_ref(ctx, parts[0])
        else:
            view = str(parts[0] if parts else "recent").strip() or "recent"
            task_id = str(parts[2] if len(parts) >= 3 else "").strip()
        ok, prefix = await _delete_task_from_menu(ctx, task_id=task_id)
        payload, ui = await _build_task_list_payload(
            ctx,
            view=view or "recent",
            prefix=prefix,
        )
    else:
        payload, ui = await _build_task_list_payload(
            ctx,
            view="recent",
            prefix="❌ 未识别的任务菜单操作。",
        )

    await edit_callback_message(ctx, payload, ui=ui)
