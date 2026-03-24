from __future__ import annotations

from typing import Any

from core.channel_user_store import channel_user_store
from core.config import is_user_admin


TOOL_FEATURE_MAP = {
    "rss_subscribe": "rss",
    "ext_rss_subscribe": "rss",
    "scheduler_manager": "scheduler",
    "ext_scheduler_manager": "scheduler",
    "stock_watch": "stock",
    "ext_stock_watch": "stock",
    "quick_accounting": "accounting",
    "ext_quick_accounting": "accounting",
}


FEATURE_LABELS = {
    "chat": "聊天",
    "rss": "RSS",
    "heartbeat": "Heartbeat",
    "scheduler": "定时任务",
    "stock": "自选股",
    "accounting": "记账",
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def feature_for_tool_name(tool_name: str) -> str:
    return TOOL_FEATURE_MAP.get(_safe_text(tool_name).lower(), "")


def is_channel_feature_enabled(
    *,
    platform: str,
    platform_user_id: str,
    feature: str,
) -> bool:
    safe_platform = _safe_text(platform).lower()
    safe_user_id = _safe_text(platform_user_id)
    safe_feature = _safe_text(feature).lower()
    if not safe_platform or not safe_user_id or not safe_feature:
        return False
    return channel_user_store.is_feature_enabled(
        platform=safe_platform,
        platform_user_id=safe_user_id,
        feature=safe_feature,
        is_admin=is_user_admin(safe_user_id),
    )


def channel_feature_denied_text(feature: str) -> str:
    label = FEATURE_LABELS.get(_safe_text(feature).lower(), "该功能")
    return f"⛔ 当前账号未开放{label}功能。"
