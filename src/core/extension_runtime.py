from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from core.platform.registry import adapter_manager

logger = logging.getLogger(__name__)


class ExtensionRuntime:
    def __init__(self, *, scheduler: Any):
        self.scheduler = scheduler
        self.adapter_manager = adapter_manager
        self.repo_root = Path(__file__).resolve().parents[2]
        self.src_root = self.repo_root / "src"
        self.extension_root = self.repo_root / "extension"
        self._startup_callbacks: list[Callable[[], Any]] = []
        self._shutdown_callbacks: list[Callable[[], Any]] = []
        self._memory_provider_name = ""
        self._memory_provider: Any = None

    def register_adapter(self, adapter: Any) -> Any:
        self.adapter_manager.register_adapter(adapter)
        return adapter

    def get_adapter(self, platform_name: str) -> Any:
        return self.adapter_manager.get_adapter(str(platform_name or "").strip().lower())

    def has_adapter(self, platform_name: str) -> bool:
        try:
            self.get_adapter(platform_name)
        except Exception:
            return False
        return True

    def list_adapters(self) -> list[str]:
        return self.adapter_manager.list_platforms()

    def register_command(
        self,
        command: str,
        handler_func: Callable,
        *,
        platforms: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        safe_platforms = [str(item).strip().lower() for item in list(platforms or []) if str(item).strip()]
        if safe_platforms:
            for platform_name in safe_platforms:
                adapter = self.get_adapter(platform_name)
                try:
                    adapter.on_command(command, handler_func, **kwargs)
                except TypeError:
                    adapter.on_command(command, handler_func)
            return
        self.adapter_manager.on_command(command, handler_func, **kwargs)

    def register_callback(
        self,
        pattern: str,
        handler_func: Callable,
        *,
        platforms: Sequence[str] | None = None,
    ) -> None:
        safe_platforms = [str(item).strip().lower() for item in list(platforms or []) if str(item).strip()]
        if safe_platforms:
            for platform_name in safe_platforms:
                adapter = self.get_adapter(platform_name)
                adapter.on_callback_query(pattern, handler_func)
            return
        self.adapter_manager.on_callback_query(pattern, handler_func)

    def register_job(self, *args: Any, **kwargs: Any) -> Any:
        return self.scheduler.add_job(*args, **kwargs)

    def on_startup(self, callback: Callable[[], Any]) -> None:
        self._startup_callbacks.append(callback)

    def on_shutdown(self, callback: Callable[[], Any]) -> None:
        self._shutdown_callbacks.append(callback)

    async def run_startup(self) -> None:
        for callback in list(self._startup_callbacks):
            result = callback()
            if inspect.isawaitable(result):
                await result

    async def run_shutdown(self) -> None:
        for callback in reversed(self._shutdown_callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning("Extension shutdown callback failed.", exc_info=True)

    def activate_memory_provider(self, provider_name: str, provider: Any) -> None:
        self._memory_provider_name = str(provider_name or "").strip().lower()
        self._memory_provider = provider

    def get_active_memory_provider_name(self) -> str:
        return self._memory_provider_name

    def get_active_memory_provider(self) -> Any:
        if self._memory_provider is None:
            raise RuntimeError("memory provider not activated")
        return self._memory_provider


extension_runtime: ExtensionRuntime | None = None


def init_extension_runtime(*, scheduler: Any) -> ExtensionRuntime:
    global extension_runtime
    extension_runtime = ExtensionRuntime(scheduler=scheduler)
    return extension_runtime


def get_extension_runtime() -> ExtensionRuntime:
    if extension_runtime is None:
        raise RuntimeError("extension runtime is not initialized")
    return extension_runtime
