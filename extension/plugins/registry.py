from __future__ import annotations

import importlib
import inspect
import logging
from pathlib import Path

from core.extension_base import PluginExtension

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self.root_dir = Path(__file__).resolve().parent
        self._extensions: list[PluginExtension] = []

    def _iter_extension_modules(self) -> list[str]:
        modules: list[str] = []
        for path in sorted(self.root_dir.glob("*.py")):
            if path.name in {"__init__.py", "registry.py"}:
                continue
            modules.append(f"extension.plugins.{path.stem}")
        return modules

    def scan_extensions(self) -> list[PluginExtension]:
        loaded: list[PluginExtension] = []
        for module_name in self._iter_extension_modules():
            try:
                module = importlib.import_module(module_name)
            except Exception:
                logger.error("Failed to import plugin module %s", module_name, exc_info=True)
                continue
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, PluginExtension)
                    and obj is not PluginExtension
                    and obj.__module__ == module.__name__
                ):
                    loaded.append(obj())
        self._extensions = sorted(
            loaded,
            key=lambda item: (int(getattr(item, "priority", 100)), getattr(item, "name", "")),
        )
        return list(self._extensions)

    def register_extensions(self, runtime) -> None:
        for extension in self.scan_extensions():
            if not extension.enabled(runtime):
                continue
            extension.register(runtime)


plugin_registry = PluginRegistry()
