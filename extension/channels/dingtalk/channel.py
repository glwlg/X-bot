from __future__ import annotations

from core.config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET
from core.extension_base import ChannelExtension
from core.runtime_config_store import runtime_config_store

from .adapter import DingTalkAdapter
from ..common import COMMON_CALLBACK_PATTERN, button_callback, route_message_by_type


class DingTalkChannelExtension(ChannelExtension):
    name = "dingtalk_channel"
    platform_name = "dingtalk"
    priority = 30

    def enabled(self, runtime) -> bool:
        _ = runtime
        return bool(
            DINGTALK_CLIENT_ID
            and DINGTALK_CLIENT_SECRET
            and runtime_config_store.is_platform_enabled("dingtalk", default=True)
        )

    def register(self, runtime) -> None:
        adapter = runtime.register_adapter(
            DingTalkAdapter(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET)
        )
        adapter.register_message_handler(route_message_by_type)
        adapter.on_callback_query(COMMON_CALLBACK_PATTERN, button_callback)
