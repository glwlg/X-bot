from typing import Dict, Type, Callable, Any
from .adapter import BotAdapter
import logging
import asyncio
import signal

logger = logging.getLogger(__name__)


class AdapterManager:
    """Manages the lifecycle of multiple bot adapters"""

    def __init__(self):
        self._adapters: Dict[str, BotAdapter] = {}

    def register_adapter(self, adapter: BotAdapter):
        """Register a new adapter instance"""
        if adapter.platform_name in self._adapters:
            logger.warning(
                f"Adapter for {adapter.platform_name} is already registered. Overwriting."
            )

        self._adapters[adapter.platform_name] = adapter
        logger.info(f"Registered adapter for platform: {adapter.platform_name}")

    def get_adapter(self, platform_name: str) -> BotAdapter:
        """Get a registered adapter by name"""
        adapter = self._adapters.get(platform_name)
        if not adapter:
            raise ValueError(f"No adapter registered for platform: {platform_name}")
        return adapter

    async def start_all(self):
        """Start all registered adapters"""
        for name, adapter in self._adapters.items():
            logger.info(f"Starting adapter: {name}")
            await adapter.start()

    async def stop_all(self):
        """Stop all registered adapters"""
        for name, adapter in self._adapters.items():
            logger.info(f"Stopping adapter: {name}")
            await adapter.stop()

    def on_command(self, command: str, handler_func: Callable):
        """Register a command handler across all adapters"""
        for adapter in self._adapters.values():
            if hasattr(adapter, "on_command"):
                adapter.on_command(command, handler_func)

    def on_message(self, filters_obj: Any, handler_func: Callable):
        """Register a message handler across all adapters"""
        for adapter in self._adapters.values():
            if hasattr(adapter, "on_message"):
                adapter.on_message(filters_obj, handler_func)

    def register_common_handlers(
        self, command_handlers: Dict[str, Callable], message_handler: Callable = None
    ):
        """Helper to batch register"""
        for cmd, func in command_handlers.items():
            self.on_command(cmd, func)

        if message_handler:
            # Universal text handler match
            # Note: filters_obj is platform specific usually.
            # We might need to abstract filters or let adapter handle default text.
            # Note: filters_obj is platform specific usually.
            # We might need to abstract filters or let adapter handle default text.
            pass


# Global Singleton
adapter_manager = AdapterManager()
