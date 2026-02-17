"""User settings repository backed by filesystem JSON."""

from __future__ import annotations

from .base import now_iso, read_json, user_path, write_json


def _settings_path(user_id: int | str):
    return user_path(user_id, "settings.md")


async def set_translation_mode(user_id: int | str, enabled: bool):
    path = _settings_path(user_id)
    current = await read_json(
        path,
        {
            "user_id": str(user_id),
            "auto_translate": 0,
            "target_lang": "zh-CN",
            "updated_at": now_iso(),
        },
    )
    if not isinstance(current, dict):
        current = {
            "user_id": str(user_id),
            "auto_translate": 0,
            "target_lang": "zh-CN",
            "updated_at": now_iso(),
        }
    current["user_id"] = str(user_id)
    current["auto_translate"] = 1 if bool(enabled) else 0
    current["target_lang"] = str(current.get("target_lang") or "zh-CN")
    current["updated_at"] = now_iso()
    await write_json(path, current)


async def get_user_settings(user_id: int | str) -> dict:
    path = _settings_path(user_id)
    current = await read_json(path, {})
    if not isinstance(current, dict):
        current = {}
    return {
        "user_id": str(user_id),
        "auto_translate": int(current.get("auto_translate") or 0),
        "target_lang": str(current.get("target_lang") or "zh-CN"),
        "updated_at": str(current.get("updated_at") or now_iso()),
    }
