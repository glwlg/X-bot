"""Allowed users repository backed by filesystem JSON."""

from __future__ import annotations

from .base import now_iso, read_json, system_path, write_json


def _allowed_path():
    return system_path("allowed_users.md")


async def _read_allowed() -> list[dict]:
    payload = await read_json(_allowed_path(), [])
    if not isinstance(payload, list):
        return []
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "user_id": str(item.get("user_id") or "").strip(),
                "added_by": str(item.get("added_by") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "created_at": str(item.get("created_at") or now_iso()),
            }
        )
    return [item for item in rows if item["user_id"]]


async def add_allowed_user(
    user_id: int | str,
    added_by: int | str | None = None,
    description: str | None = None,
):
    uid = str(user_id).strip()
    if not uid:
        return
    rows = await _read_allowed()
    if any(item["user_id"] == uid for item in rows):
        return
    rows.append(
        {
            "user_id": uid,
            "added_by": str(added_by or "").strip(),
            "description": str(description or "").strip(),
            "created_at": now_iso(),
        }
    )
    await write_json(_allowed_path(), rows)


async def remove_allowed_user(user_id: int | str):
    uid = str(user_id).strip()
    rows = await _read_allowed()
    kept = [item for item in rows if item["user_id"] != uid]
    if len(kept) != len(rows):
        await write_json(_allowed_path(), kept)


async def get_allowed_users() -> list[dict]:
    return await _read_allowed()


async def check_user_allowed_in_db(user_id: int | str) -> bool:
    uid = str(user_id).strip()
    rows = await _read_allowed()
    return any(item["user_id"] == uid for item in rows)
