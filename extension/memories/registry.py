from __future__ import annotations

import importlib
import inspect
import logging
from pathlib import Path

from core.extension_base import MemoryExtension

logger = logging.getLogger(__name__)


class MemoryRegistry:
    def __init__(self) -> None:
        self.root_dir = Path(__file__).resolve().parent
        self._extensions: list[MemoryExtension] = []

    def _iter_extension_modules(self) -> list[str]:
        modules: list[str] = []
        for path in sorted(self.root_dir.glob("*.py")):
            if path.name in {"__init__.py", "registry.py"}:
                continue
            modules.append(f"extension.memories.{path.stem}")
        return modules

    def scan_extensions(self) -> list[MemoryExtension]:
        loaded: list[MemoryExtension] = []
        for module_name in self._iter_extension_modules():
            try:
                module = importlib.import_module(module_name)
            except Exception:
                logger.error("Failed to import memory module %s", module_name, exc_info=True)
                continue
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, MemoryExtension)
                    and obj is not MemoryExtension
                    and obj.__module__ == module.__name__
                ):
                    loaded.append(obj())
        self._extensions = sorted(
            loaded,
            key=lambda item: (int(getattr(item, "priority", 100)), getattr(item, "name", "")),
        )
        return list(self._extensions)

    def activate_extension(self, runtime) -> MemoryExtension:
        enabled = [item for item in self.scan_extensions() if item.enabled(runtime)]
        if len(enabled) != 1:
            names = ", ".join(str(getattr(item, "name", "")) for item in enabled) or "<none>"
            raise RuntimeError(
                f"expected exactly one enabled memory extension, got {len(enabled)}: {names}"
            )
        extension = enabled[0]
        provider = extension.create_provider(runtime)
        runtime.activate_memory_provider(
            getattr(extension, "provider_name", "") or getattr(extension, "name", ""),
            provider,
        )
        return extension


memory_registry = MemoryRegistry()
