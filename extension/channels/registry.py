from __future__ import annotations

import importlib
import inspect
import logging
from pathlib import Path

from core.extension_base import ChannelExtension

logger = logging.getLogger(__name__)


class ChannelRegistry:
    def __init__(self) -> None:
        self.root_dir = Path(__file__).resolve().parent
        self._extensions: list[ChannelExtension] = []

    def _iter_extension_modules(self) -> list[str]:
        modules: list[str] = []
        for path in sorted(self.root_dir.iterdir()):
            if not path.is_dir() or path.name.startswith("__"):
                continue
            module_name = f"extension.channels.{path.name}.channel"
            if (path / "channel.py").exists():
                modules.append(module_name)
        return modules

    def scan_extensions(self) -> list[ChannelExtension]:
        loaded: list[ChannelExtension] = []
        for module_name in self._iter_extension_modules():
            try:
                module = importlib.import_module(module_name)
            except Exception:
                logger.error("Failed to import channel module %s", module_name, exc_info=True)
                continue
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, ChannelExtension)
                    and obj is not ChannelExtension
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


channel_registry = ChannelRegistry()
