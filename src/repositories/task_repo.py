"""Scheduled tasks repository backed by per-user Markdown files."""

from __future__ import annotations

from typing import Any

from .base import all_user_ids, next_id, now_iso, read_json, user_path, write_json


def _tasks_path(user_id: int | str):
    return user_path(user_id, "automation", "scheduled_tasks.md")


def _normalize(raw: dict[str, Any], *, user_id: int | str) -> dict[str, Any]:
    return {
        "id": int(raw.get("id") or 0),
        "user_id": str(user_id),
        "crontab": str(raw.get("crontab") or "").strip(),
        "instruction": str(raw.get("instruction") or "").strip(),
        "platform": str(raw.get("platform") or "telegram").strip() or "telegram",
        "need_push": bool(raw.get("need_push", True)),
        "is_active": bool(raw.get("is_active", True)),
        "created_at": str(raw.get("created_at") or now_iso()),
        "updated_at": str(raw.get("updated_at") or now_iso()),
    }


async def _read_user_tasks(user_id: int | str) -> list[dict[str, Any]]:
    data = await read_json(_tasks_path(user_id), [])
    if not isinstance(data, list):
        return []
    return [
        _normalize(item, user_id=user_id) for item in data if isinstance(item, dict)
    ]


async def _write_user_tasks(user_id: int | str, rows: list[dict[str, Any]]) -> None:
    payload = []
    for row in rows:
        payload.append(
            {
                "id": int(row.get("id") or 0),
                "crontab": str(row.get("crontab") or "").strip(),
                "instruction": str(row.get("instruction") or "").strip(),
                "platform": str(row.get("platform") or "telegram"),
                "need_push": bool(row.get("need_push", True)),
                "is_active": bool(row.get("is_active", True)),
                "created_at": str(row.get("created_at") or now_iso()),
                "updated_at": str(row.get("updated_at") or now_iso()),
            }
        )
    await write_json(_tasks_path(user_id), payload)


async def add_scheduled_task(
    crontab: str,
    instruction: str,
    user_id: int | str = 0,
    platform: str = "telegram",
    need_push: bool = True,
) -> int:
    uid = str(user_id or "0")
    rows = await _read_user_tasks(uid)
    tid = await next_id("scheduled_task", start=1)
    rows.append(
        {
            "id": int(tid),
            "user_id": uid,
            "crontab": str(crontab or "").strip(),
            "instruction": str(instruction or "").strip(),
            "platform": str(platform or "telegram"),
            "need_push": bool(need_push),
            "is_active": True,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    await _write_user_tasks(uid, rows)
    return int(tid)


async def get_all_active_tasks(user_id: int | str | None = None) -> list[dict]:
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    merged: list[dict[str, Any]] = []
    for uid in target_users:
        rows = await _read_user_tasks(uid)
        merged.extend([item for item in rows if bool(item.get("is_active", True))])
    return merged


async def update_task_status(
    task_id: int, is_active: bool, user_id: int | str | None = None
):
    tid = int(task_id)
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        rows = await _read_user_tasks(uid)
        changed = False
        for item in rows:
            if int(item.get("id") or 0) != tid:
                continue
            item["is_active"] = bool(is_active)
            item["updated_at"] = now_iso()
            changed = True
            break
        if changed:
            await _write_user_tasks(uid, rows)
            return


async def delete_task(task_id: int, user_id: int | str | None = None):
    tid = int(task_id)
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        rows = await _read_user_tasks(uid)
        kept = [item for item in rows if int(item.get("id") or 0) != tid]
        if len(kept) != len(rows):
            await _write_user_tasks(uid, kept)
            return
