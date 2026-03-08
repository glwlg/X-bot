from __future__ import annotations

from typing import Any


_CALLBACKS_ATTR = "_xbot_runtime_callbacks"


def _ensure_store(ctx: Any) -> dict[str, Any]:
    store = getattr(ctx, _CALLBACKS_ATTR, None)
    if isinstance(store, dict):
        return store
    store = {}
    setattr(ctx, _CALLBACKS_ATTR, store)
    return store


def set_runtime_callback(ctx: Any, name: str, callback: Any) -> Any:
    safe_name = str(name or "").strip()
    if not safe_name:
        return callback
    store = _ensure_store(ctx)
    store[safe_name] = callback
    return callback


def get_runtime_callback(ctx: Any, name: str) -> Any:
    safe_name = str(name or "").strip()
    if not safe_name:
        return None
    store = getattr(ctx, _CALLBACKS_ATTR, None)
    if not isinstance(store, dict):
        return None
    return store.get(safe_name)


def pop_runtime_callback(ctx: Any, name: str) -> Any:
    safe_name = str(name or "").strip()
    if not safe_name:
        return None
    store = getattr(ctx, _CALLBACKS_ATTR, None)
    if not isinstance(store, dict):
        return None
    return store.pop(safe_name, None)
