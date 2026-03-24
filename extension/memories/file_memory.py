from __future__ import annotations

from core.extension_base import MemoryExtension
from core.markdown_memory_store import markdown_memory_store
from core.memory_config import get_memory_provider_name


class FileMemoryExtension(MemoryExtension):
    name = "file_memory"
    provider_name = "file"
    priority = 10

    def enabled(self, runtime) -> bool:
        return get_memory_provider_name() == self.provider_name

    def create_provider(self, runtime):
        return markdown_memory_store
