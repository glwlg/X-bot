from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

FEED_PROVIDER_NATIVE = "native_rss"
FEED_PROVIDER_RSSHUB = "rsshub"
FEED_PROVIDER_RSS_BRIDGE = "rss_bridge"

VALID_FEED_PROVIDERS = {
    FEED_PROVIDER_NATIVE,
    FEED_PROVIDER_RSSHUB,
    FEED_PROVIDER_RSS_BRIDGE,
}


def normalize_platform(value: Any) -> str:
    platform = str(value or "telegram").strip().lower()
    return platform or "telegram"


def detect_feed_provider(feed_url: str) -> str:
    parsed = urlparse(str(feed_url or "").strip())
    host = (parsed.netloc or "").lower()
    if "rsshub" in host:
        return FEED_PROVIDER_RSSHUB
    if "rss-bridge" in host or "rssbridge" in host:
        return FEED_PROVIDER_RSS_BRIDGE
    return FEED_PROVIDER_NATIVE


def normalize_provider(provider: Any, *, feed_url: str = "") -> str:
    raw = str(provider or "").strip().lower()
    if not raw:
        return detect_feed_provider(feed_url)
    if raw not in VALID_FEED_PROVIDERS:
        raise ValueError("feed provider must be one of: native_rss, rsshub, rss_bridge")
    return raw


def default_title(*, feed_url: str = "") -> str:
    return str(feed_url or "").strip()
