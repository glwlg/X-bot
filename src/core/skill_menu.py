from __future__ import annotations

from typing import Any, Iterable


_MENU_CACHE_KEY = "__skill_menus__"


def make_callback(namespace: str, action: str, *parts: object) -> str:
    tokens = [str(namespace or "").strip(), str(action or "").strip()]
    tokens.extend(str(part).strip() for part in parts if str(part).strip())
    return "_".join(token for token in tokens if token)


def parse_callback(data: str | None, namespace: str) -> tuple[str, list[str]]:
    raw = str(data or "").strip()
    prefix = f"{str(namespace or '').strip()}_"
    if not raw.startswith(prefix):
        return "", []
    parts = raw.split("_")
    if len(parts) < 2:
        return "", []
    return parts[1], parts[2:]


def button_rows(
    buttons: Iterable[dict[str, Any]],
    *,
    columns: int = 2,
) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    row: list[dict[str, Any]] = []

    width = max(1, int(columns or 1))
    for button in buttons:
        row.append(dict(button))
        if len(row) >= width:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return rows


def menu_store(ctx: Any, namespace: str) -> dict[str, Any]:
    user_data = getattr(ctx, "user_data", None)
    if user_data is None:
        return {}
    root = user_data.setdefault(_MENU_CACHE_KEY, {})
    namespace_key = str(namespace or "").strip()
    if namespace_key not in root or not isinstance(root[namespace_key], dict):
        root[namespace_key] = {}
    return root[namespace_key]


def cache_items(ctx: Any, namespace: str, key: str, items: Iterable[Any]) -> list[Any]:
    cached = list(items)
    menu_store(ctx, namespace)[str(key or "").strip()] = cached
    return cached


def get_cached_items(ctx: Any, namespace: str, key: str) -> list[Any]:
    items = menu_store(ctx, namespace).get(str(key or "").strip(), [])
    return items if isinstance(items, list) else []


def get_cached_item(
    ctx: Any,
    namespace: str,
    key: str,
    index: str | int | None,
) -> Any:
    try:
        resolved_index = int(str(index or "").strip())
    except Exception:
        return None

    items = get_cached_items(ctx, namespace, key)
    if resolved_index < 0 or resolved_index >= len(items):
        return None
    return items[resolved_index]
