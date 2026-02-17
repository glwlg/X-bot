"""Video cache repository backed by filesystem JSON."""

from __future__ import annotations

from .base import now_iso, read_json, system_path, write_json


def _cache_path():
    return system_path("video_cache.md")


async def save_video_cache(file_id: str, file_path: str):
    payload = await read_json(_cache_path(), {})
    if not isinstance(payload, dict):
        payload = {}
    fid = str(file_id or "").strip()
    if not fid:
        return
    payload[fid] = {
        "file_path": str(file_path or "").strip(),
        "created_at": now_iso(),
    }
    await write_json(_cache_path(), payload)


async def get_video_cache(file_id: str) -> str | None:
    payload = await read_json(_cache_path(), {})
    if not isinstance(payload, dict):
        return None
    item = payload.get(str(file_id or "").strip())
    if isinstance(item, dict):
        return str(item.get("file_path") or "") or None
    if isinstance(item, str):
        return item or None
    return None
