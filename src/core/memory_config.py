from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MEMORY_CONFIG_PATH = os.getenv("MEMORY_CONFIG_PATH", "config/memory.json")


@dataclass
class MemoryConfig:
    provider: str = "file"
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get_provider_settings(self, provider_name: str | None = None) -> dict[str, Any]:
        name = str(provider_name or self.provider or "file").strip().lower() or "file"
        payload = self.providers.get(name)
        if isinstance(payload, dict):
            return dict(payload)
        return {}


_memory_config: MemoryConfig | None = None


def load_memory_config(config_path: str | None = None) -> MemoryConfig:
    global _memory_config

    if _memory_config is not None:
        return _memory_config

    path = Path(config_path or os.getenv("MEMORY_CONFIG_PATH", MEMORY_CONFIG_PATH))
    if not path.exists():
        _memory_config = MemoryConfig(
            provider="file",
            providers={"file": {}, "mem0": {}},
        )
        return _memory_config

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    provider = str(raw.get("provider") or "file").strip().lower() or "file"
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    normalized = {
        str(name or "").strip().lower(): dict(payload)
        for name, payload in providers.items()
        if str(name or "").strip() and isinstance(payload, dict)
    }
    if provider not in normalized:
        normalized.setdefault(provider, {})

    _memory_config = MemoryConfig(provider=provider, providers=normalized)
    return _memory_config


def get_memory_provider_name() -> str:
    return load_memory_config().provider


def reset_memory_config_cache() -> None:
    global _memory_config
    _memory_config = None
