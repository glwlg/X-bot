"""RSS subscriptions repository backed by user-scoped Markdown files."""

from __future__ import annotations

from typing import Any

from .base import all_user_ids, read_json, user_path, write_json


def _subs_path(user_id: int | str):
    return user_path(user_id, "rss", "subscriptions.md")


def _normalize_subscription(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "feed_url": str(raw.get("feed_url") or "").strip(),
        "title": str(raw.get("title") or "").strip(),
        "platform": str(raw.get("platform") or "telegram").strip() or "telegram",
        "last_etag": str(raw.get("last_etag") or "").strip(),
        "last_modified": str(raw.get("last_modified") or "").strip(),
        "last_entry_hash": str(raw.get("last_entry_hash") or "").strip(),
    }


def _to_runtime_rows(
    user_id: int | str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    runtime: list[dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        feed_url = str(item.get("feed_url") or "").strip()
        if not feed_url:
            continue
        runtime.append(
            {
                "id": index,
                "user_id": str(user_id),
                "feed_url": feed_url,
                "title": str(item.get("title") or feed_url),
                "platform": str(item.get("platform") or "telegram"),
                "last_etag": str(item.get("last_etag") or ""),
                "last_modified": str(item.get("last_modified") or ""),
                "last_entry_hash": str(item.get("last_entry_hash") or ""),
            }
        )
    return runtime


async def _read_user_subscriptions(user_id: int | str) -> list[dict[str, Any]]:
    path = _subs_path(user_id)
    data = await read_json(path, [])
    if not isinstance(data, list):
        return []
    normalized = [
        _normalize_subscription(item) for item in data if isinstance(item, dict)
    ]
    return [item for item in normalized if item.get("feed_url")]


async def _write_user_subscriptions(
    user_id: int | str, rows: list[dict[str, Any]]
) -> None:
    payload: list[dict[str, Any]] = []
    for row in rows:
        feed_url = str(row.get("feed_url") or "").strip()
        if not feed_url:
            continue
        payload.append(
            {
                "feed_url": feed_url,
                "title": str(row.get("title") or feed_url),
                "platform": str(row.get("platform") or "telegram"),
                "last_etag": str(row.get("last_etag") or ""),
                "last_modified": str(row.get("last_modified") or ""),
                "last_entry_hash": str(row.get("last_entry_hash") or ""),
            }
        )
    await write_json(_subs_path(user_id), payload)


async def add_subscription(
    user_id: int | str,
    feed_url: str,
    title: str,
    platform: str = "telegram",
):
    rows = await _read_user_subscriptions(user_id)
    target_url = str(feed_url or "").strip()
    if not target_url:
        raise ValueError("feed_url is required")
    if any(str(item.get("feed_url") or "").strip() == target_url for item in rows):
        raise ValueError(
            "UNIQUE constraint failed: subscriptions.user_id, subscriptions.feed_url"
        )

    rows.append(
        {
            "feed_url": target_url,
            "title": str(title or target_url),
            "platform": str(platform or "telegram"),
            "last_etag": "",
            "last_modified": "",
            "last_entry_hash": "",
        }
    )
    await _write_user_subscriptions(user_id, rows)


async def delete_subscription(user_id: int | str, feed_url: str) -> bool:
    rows = await _read_user_subscriptions(user_id)
    target = str(feed_url or "").strip()
    kept = [item for item in rows if str(item.get("feed_url") or "").strip() != target]
    changed = len(kept) != len(rows)
    if changed:
        await _write_user_subscriptions(user_id, kept)
    return changed


async def delete_subscription_by_id(sub_id: int, user_id: int | str) -> bool:
    sid = int(sub_id)
    rows = await _read_user_subscriptions(user_id)
    runtime_rows = _to_runtime_rows(user_id, rows)
    target = next(
        (item for item in runtime_rows if int(item.get("id") or 0) == sid), None
    )
    if not target:
        return False
    return await delete_subscription(user_id, str(target.get("feed_url") or ""))


async def get_user_subscriptions(user_id: int | str) -> list[dict]:
    rows = await _read_user_subscriptions(user_id)
    return _to_runtime_rows(user_id, rows)


async def get_all_subscriptions() -> list[dict]:
    merged: list[dict[str, Any]] = []
    for uid in all_user_ids():
        path = _subs_path(uid)
        if not path.exists():
            continue
        rows = await _read_user_subscriptions(uid)
        merged.extend(_to_runtime_rows(uid, rows))
    return merged


async def update_subscription_status(
    user_id: int | str,
    feed_url: str,
    last_entry_hash: str,
    last_etag: str | None = None,
    last_modified: str | None = None,
):
    rows = await _read_user_subscriptions(user_id)
    target_url = str(feed_url or "").strip()
    changed = False
    for item in rows:
        if str(item.get("feed_url") or "").strip() != target_url:
            continue
        item["last_entry_hash"] = str(last_entry_hash or "")
        item["last_etag"] = str(last_etag or "")
        item["last_modified"] = str(last_modified or "")
        changed = True
        break
    if changed:
        await _write_user_subscriptions(user_id, rows)
