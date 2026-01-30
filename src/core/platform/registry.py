from typing import Dict, Type
from .adapter import BotAdapter
import logging

logger = logging.getLogger(__name__)

class AdapterManager:
    """Manages the lifecycle of multiple bot adapters"""
    
    def __init__(self):
        self._adapters: Dict[str, BotAdapter] = {}
        
    def register_adapter(self, adapter: BotAdapter):
        """Register a new adapter instance"""
        if adapter.platform_name in self._adapters:
            logger.warning(f"Adapter for {adapter.platform_name} is already registered. Overwriting.")
        
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
