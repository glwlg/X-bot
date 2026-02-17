"""Reminder repository backed by per-user Markdown files."""

from __future__ import annotations

from typing import Any

from .base import all_user_ids, next_id, now_iso, read_json, user_path, write_json


def _reminders_path(user_id: int | str):
    return user_path(user_id, "automation", "reminders.md")


def _normalize(raw: dict[str, Any], *, user_id: int | str) -> dict[str, Any]:
    return {
        "id": int(raw.get("id") or 0),
        "user_id": str(user_id),
        "chat_id": str(raw.get("chat_id") or ""),
        "message": str(raw.get("message") or ""),
        "trigger_time": str(raw.get("trigger_time") or ""),
        "created_at": str(raw.get("created_at") or now_iso()),
        "platform": str(raw.get("platform") or "telegram"),
    }


async def _read_user_reminders(user_id: int | str) -> list[dict[str, Any]]:
    data = await read_json(_reminders_path(user_id), [])
    if not isinstance(data, list):
        return []
    return [
        _normalize(item, user_id=user_id) for item in data if isinstance(item, dict)
    ]


async def _write_user_reminders(user_id: int | str, rows: list[dict[str, Any]]) -> None:
    payload = []
    for row in rows:
        payload.append(
            {
                "id": int(row.get("id") or 0),
                "chat_id": str(row.get("chat_id") or ""),
                "message": str(row.get("message") or ""),
                "trigger_time": str(row.get("trigger_time") or ""),
                "created_at": str(row.get("created_at") or now_iso()),
                "platform": str(row.get("platform") or "telegram"),
            }
        )
    await write_json(_reminders_path(user_id), payload)


async def add_reminder(
    user_id: int | str,
    chat_id: int | str,
    message: str,
    trigger_time: str,
    platform: str = "telegram",
) -> int:
    uid = str(user_id)
    rows = await _read_user_reminders(uid)
    rid = await next_id("reminder", start=1)
    rows.append(
        {
            "id": int(rid),
            "user_id": uid,
            "chat_id": str(chat_id),
            "message": str(message or ""),
            "trigger_time": str(trigger_time or ""),
            "created_at": now_iso(),
            "platform": str(platform or "telegram"),
        }
    )
    await _write_user_reminders(uid, rows)
    return int(rid)


async def delete_reminder(reminder_id: int, user_id: int | str | None = None):
    rid = int(reminder_id)
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        rows = await _read_user_reminders(uid)
        kept = [item for item in rows if int(item.get("id") or 0) != rid]
        if len(kept) != len(rows):
            await _write_user_reminders(uid, kept)
            return


async def get_pending_reminders(user_id: int | str | None = None) -> list[dict]:
    merged: list[dict[str, Any]] = []
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        merged.extend(await _read_user_reminders(uid))
    return sorted(merged, key=lambda item: str(item.get("trigger_time") or ""))
