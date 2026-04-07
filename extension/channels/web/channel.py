from __future__ import annotations

from core.extension_base import ChannelExtension
from core.runtime_config_store import runtime_config_store

from .adapter import WebAdapter
from ..common import COMMON_CALLBACK_PATTERN, button_callback, route_message_by_type


class WebChannelExtension(ChannelExtension):
    name = "web_channel"
    platform_name = "web"
    # Register the web adapter before channel extensions that selectively bind
    # commands to already-available platforms, such as weixin `/wxbind`.
    priority = 35

    def enabled(self, runtime) -> bool:
        _ = runtime
        return runtime_config_store.is_platform_enabled(
            "web",
            default=True,
        )

    def register(self, runtime) -> None:
        adapter = runtime.register_adapter(WebAdapter())
        adapter.register_message_handler(route_message_by_type)
        adapter.on_callback_query(COMMON_CALLBACK_PATTERN, button_callback)
