"""Watchlist repository backed by user-scoped Markdown files."""

from __future__ import annotations

from typing import Any

from .base import all_user_ids, read_json, user_path, write_json


def _watchlist_path(user_id: int | str):
    return user_path(user_id, "stock", "watchlist.md")


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": str(raw.get("stock_code") or "").strip(),
        "stock_name": str(raw.get("stock_name") or "").strip(),
        "platform": str(raw.get("platform") or "telegram").strip() or "telegram",
    }


def _to_runtime_rows(
    user_id: int | str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    runtime: list[dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        code = str(item.get("stock_code") or "").strip()
        if not code:
            continue
        runtime.append(
            {
                "id": index,
                "user_id": str(user_id),
                "stock_code": code,
                "stock_name": str(item.get("stock_name") or code),
                "platform": str(item.get("platform") or "telegram"),
            }
        )
    return runtime


async def _read_watchlist(user_id: int | str) -> list[dict[str, Any]]:
    data = await read_json(_watchlist_path(user_id), [])
    if not isinstance(data, list):
        return []
    rows = [_normalize(item) for item in data if isinstance(item, dict)]
    return [item for item in rows if item.get("stock_code")]


async def _write_watchlist(user_id: int | str, rows: list[dict[str, Any]]) -> None:
    payload: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("stock_code") or "").strip()
        if not code:
            continue
        payload.append(
            {
                "stock_code": code,
                "stock_name": str(row.get("stock_name") or code).strip(),
                "platform": str(row.get("platform") or "telegram").strip()
                or "telegram",
            }
        )
    await write_json(_watchlist_path(user_id), payload)


async def add_watchlist_stock(
    user_id: int | str,
    stock_code: str,
    stock_name: str,
    platform: str = "telegram",
) -> bool:
    rows = await _read_watchlist(user_id)
    code = str(stock_code or "").strip()
    if not code:
        return False
    if any(str(item.get("stock_code") or "").strip() == code for item in rows):
        return False

    rows.append(
        {
            "stock_code": code,
            "stock_name": str(stock_name or code).strip(),
            "platform": str(platform or "telegram"),
        }
    )
    await _write_watchlist(user_id, rows)
    return True


async def remove_watchlist_stock(user_id: int | str, stock_code: str) -> bool:
    rows = await _read_watchlist(user_id)
    code = str(stock_code or "").strip()
    kept = [item for item in rows if str(item.get("stock_code") or "").strip() != code]
    changed = len(kept) != len(rows)
    if changed:
        await _write_watchlist(user_id, kept)
    return changed


async def get_user_watchlist(
    user_id: int | str, platform: str | None = None
) -> list[dict]:
    rows = await _read_watchlist(user_id)
    if platform:
        target = str(platform).strip().lower()
        rows = [
            item
            for item in rows
            if str(item.get("platform") or "telegram").strip().lower() == target
        ]
    return _to_runtime_rows(user_id, rows)


async def get_all_watchlist_users() -> list[tuple[int | str, str]]:
    pairs: list[tuple[int | str, str]] = []
    seen: set[tuple[str, str]] = set()
    for uid in all_user_ids():
        rows = await _read_watchlist(uid)
        for row in rows:
            plat = str(row.get("platform") or "telegram")
            key = (str(uid), plat)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((uid, plat))
    return pairs
