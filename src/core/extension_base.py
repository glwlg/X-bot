from __future__ import annotations

from typing import Any


class BaseExtension:
    name = ""
    priority = 100

    def enabled(self, runtime: Any) -> bool:
        return True

    def register(self, runtime: Any) -> None:
        raise NotImplementedError


class SkillExtension(BaseExtension):
    skill_name = ""


class ChannelExtension(BaseExtension):
    platform_name = ""


class MemoryExtension(BaseExtension):
    provider_name = ""

    def create_provider(self, runtime: Any) -> Any:
        raise NotImplementedError


class PluginExtension(BaseExtension):
    pass
