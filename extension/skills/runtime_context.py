from __future__ import annotations

import shlex
from typing import Any, Dict


def dispatch_context(params: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(params or {})
    raw = payload.get("_ikaros_dispatch")
    return dict(raw) if isinstance(raw, dict) else {}


def user_request(ctx: Any, params: Dict[str, Any] | None = None) -> str:
    context = dispatch_context(params)
    request = str(context.get("user_request") or "").strip()
    if request:
        return request
    message = getattr(ctx, "message", None)
    text = str(getattr(message, "text", "") or "").strip()
    if text:
        return text
    user_data = getattr(ctx, "user_data", None)
    if isinstance(user_data, dict):
        fallback = str(user_data.get("task_goal") or "").strip()
        if fallback:
            return fallback
    return ""


def notify_target(ctx: Any, params: Dict[str, Any] | None = None) -> Dict[str, str]:
    context = dispatch_context(params)
    message = getattr(ctx, "message", None)
    msg_user = getattr(message, "user", None)
    msg_chat = getattr(message, "chat", None)
    user_data = getattr(ctx, "user_data", None)
    user_payload = user_data if isinstance(user_data, dict) else {}

    platform = str(
        context.get("notify_platform") or getattr(message, "platform", "") or ""
    ).strip()
    chat_id = str(context.get("notify_chat_id") or getattr(msg_chat, "id", "") or "").strip()
    user_id = str(context.get("notify_user_id") or getattr(msg_user, "id", "") or "").strip()

    forced_platform = str(user_payload.get("subagent_delivery_platform") or "").strip()
    forced_chat_id = str(user_payload.get("subagent_delivery_chat_id") or "").strip()
    if forced_platform:
        platform = forced_platform
    if forced_chat_id:
        chat_id = forced_chat_id

    return {
        "notify_platform": platform,
        "notify_chat_id": chat_id,
        "notify_user_id": user_id,
    }


def runtime_user_id(ctx: Any, params: Dict[str, Any] | None = None) -> str:
    context = dispatch_context(params)
    value = str(context.get("runtime_user_id") or "").strip()
    if value:
        return value
    message = getattr(ctx, "message", None)
    msg_user = getattr(message, "user", None)
    return str(getattr(msg_user, "id", "") or "").strip()


def task_inbox_id(params: Dict[str, Any] | None = None) -> str:
    context = dispatch_context(params)
    return str(context.get("task_inbox_id") or "").strip()


def normalize_cli_argv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            return [
                str(item).strip() for item in shlex.split(value) if str(item).strip()
            ]
        except Exception:
            return [item.strip() for item in value.split() if item.strip()]
    return []
