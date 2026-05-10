from __future__ import annotations

from typing import Any

from core.storage_service import (
    dedupe_rows,
    now_iso,
    read_row_list,
    storage_service,
    user_state_path,
)


def _scheduled_tasks_path(user_id: int | str):
    return user_state_path(user_id, "scheduler_manager", "scheduled_tasks.md")


def _normalize_scheduled_task(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(raw.get("id") or 0),
        "crontab": str(raw.get("crontab") or "").strip(),
        "instruction": str(raw.get("instruction") or "").strip(),
        "platform": str(raw.get("platform") or "telegram").strip() or "telegram",
        "chat_id": str(raw.get("chat_id") or "").strip(),
        "session_id": str(raw.get("session_id") or "").strip(),
        "need_push": bool(raw.get("need_push", True)),
        "is_active": bool(raw.get("is_active", True)),
        "created_at": str(raw.get("created_at") or now_iso()),
        "updated_at": str(raw.get("updated_at") or now_iso()),
    }


async def _read_user_scheduled_tasks(user_id: int | str) -> list[dict[str, Any]]:
    current_rows = read_row_list(
        await storage_service.read(_scheduled_tasks_path(user_id), []),
        "scheduled_tasks",
        "tasks",
    )
    return dedupe_rows(
        [
            _normalize_scheduled_task(item)
            for item in current_rows
            if isinstance(item, dict)
        ],
        key_fn=lambda row: int(row.get("id") or 0),
    )


async def _write_user_scheduled_tasks(
    user_id: int | str,
    rows: list[dict[str, Any]],
) -> None:
    payload: list[dict[str, Any]] = []
    for row in dedupe_rows(rows, key_fn=lambda item: int(item.get("id") or 0)):
        serialized = {
            "id": int(row.get("id") or 0),
            "crontab": str(row.get("crontab") or "").strip(),
            "instruction": str(row.get("instruction") or "").strip(),
            "platform": str(row.get("platform") or "telegram"),
            "need_push": bool(row.get("need_push", True)),
            "is_active": bool(row.get("is_active", True)),
            "created_at": str(row.get("created_at") or now_iso()),
            "updated_at": str(row.get("updated_at") or now_iso()),
        }
        if str(row.get("chat_id") or "").strip():
            serialized["chat_id"] = str(row.get("chat_id") or "").strip()
        if str(row.get("session_id") or "").strip():
            serialized["session_id"] = str(row.get("session_id") or "").strip()
        payload.append(serialized)
    payload.sort(key=lambda item: int(item.get("id") or 0))
    await storage_service.write(_scheduled_tasks_path(user_id), payload)


async def add_scheduled_task(
    crontab: str,
    instruction: str,
    user_id: int | str = 0,
    platform: str = "telegram",
    chat_id: str = "",
    session_id: str = "",
    need_push: bool = True,
) -> int:
    rows = await _read_user_scheduled_tasks(user_id or "")
    task_id = await storage_service.next_id_after_store_rows(
        "scheduled_task",
        _scheduled_tasks_path(""),
        list_keys=("scheduled_tasks", "tasks"),
    )
    rows.append(
        {
            "id": int(task_id),
            "crontab": str(crontab or "").strip(),
            "instruction": str(instruction or "").strip(),
            "platform": str(platform or "telegram"),
            "chat_id": str(chat_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "need_push": bool(need_push),
            "is_active": True,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    await _write_user_scheduled_tasks(user_id or "", rows)
    return int(task_id)


async def get_all_active_tasks(
    user_id: int | str | None = None,
) -> list[dict[str, Any]]:
    rows = await get_all_scheduled_tasks(user_id)
    return [item for item in rows if bool(item.get("is_active", True))]


async def get_all_scheduled_tasks(
    user_id: int | str | None = None,
) -> list[dict[str, Any]]:
    return await _read_user_scheduled_tasks(user_id or "")


async def update_task_status(
    task_id: int,
    is_active: bool,
    user_id: int | str | None = None,
) -> bool:
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
    changed = False
    for item in rows:
        if int(item.get("id") or 0) != tid:
            continue
        item["is_active"] = bool(is_active)
        item["updated_at"] = now_iso()
        changed = True
        break
    if changed:
        await _write_user_scheduled_tasks(user_id or "", rows)
        return True
    return False


async def update_task_delivery_target(
    task_id: int,
    user_id: int | str | None = None,
    *,
    platform: str,
    chat_id: str,
    session_id: str = "",
) -> bool:
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
    changed = False
    for item in rows:
        if int(item.get("id") or 0) != tid:
            continue
        item["platform"] = str(platform or "telegram").strip() or "telegram"
        item["chat_id"] = str(chat_id or "").strip()
        item["session_id"] = str(session_id or "").strip()
        item["updated_at"] = now_iso()
        changed = True
        break
    if changed:
        await _write_user_scheduled_tasks(user_id or "", rows)
        return True
    return False


async def delete_task(task_id: int, user_id: int | str | None = None) -> None:
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
    kept = [item for item in rows if int(item.get("id") or 0) != tid]
    if len(kept) != len(rows):
        await _write_user_scheduled_tasks(user_id or "", kept)


async def update_scheduled_task(
    task_id: int,
    user_id: int | str | None = None,
    crontab: str | None = None,
    instruction: str | None = None,
) -> bool:
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
    changed = False
    for item in rows:
        if int(item.get("id") or 0) != tid:
            continue
        if crontab is not None:
            item["crontab"] = str(crontab).strip()
        if instruction is not None:
            item["instruction"] = str(instruction).strip()
        item["updated_at"] = now_iso()
        changed = True
        break
    if changed:
        await _write_user_scheduled_tasks(user_id or "", rows)
        return True
    return False


__all__ = [
    "add_scheduled_task",
    "delete_task",
    "get_all_active_tasks",
    "get_all_scheduled_tasks",
    "update_scheduled_task",
    "update_task_delivery_target",
    "update_task_status",
]
