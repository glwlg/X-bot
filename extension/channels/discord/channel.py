from __future__ import annotations

from core.config import DISCORD_BOT_TOKEN
from core.extension_base import ChannelExtension
from core.runtime_config_store import runtime_config_store

from .adapter import DiscordAdapter
from ..common import COMMON_CALLBACK_PATTERN, button_callback, route_message_by_type


class DiscordChannelExtension(ChannelExtension):
    name = "discord_channel"
    platform_name = "discord"
    priority = 20

    def enabled(self, runtime) -> bool:
        _ = runtime
        return bool(DISCORD_BOT_TOKEN) and runtime_config_store.is_platform_enabled(
            "discord",
            default=True,
        )

    def register(self, runtime) -> None:
        adapter = runtime.register_adapter(DiscordAdapter(DISCORD_BOT_TOKEN))
        adapter.register_message_handler(route_message_by_type)
        adapter.on_callback_query(COMMON_CALLBACK_PATTERN, button_callback)
