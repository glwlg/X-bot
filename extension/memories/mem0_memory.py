from __future__ import annotations

import re
from typing import Any

from core.extension_base import MemoryExtension
from core.memory_config import get_memory_provider_name, load_memory_config

MemoryItem = dict[str, Any]


class Mem0LongTermMemoryProvider:
    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = dict(settings or {})
        self._memory: Any = None
        self._ikaros_agent_id = "core-ikaros"

    async def initialize(self) -> None:
        try:
            from mem0 import AsyncMemory
        except Exception as exc:
            raise RuntimeError(
                "mem0 provider selected but mem0ai is not installed"
            ) from exc

        kwargs = dict(self.settings.get("kwargs") or {})
        raw_config = self.settings.get("config")
        if isinstance(raw_config, dict) and raw_config:
            try:
                from mem0.configs.base import MemoryConfig
            except Exception as exc:
                raise RuntimeError(
                    "mem0 provider config requires mem0.configs.base.MemoryConfig"
                ) from exc
            kwargs["config"] = MemoryConfig(**raw_config)

        try:
            self._memory = AsyncMemory(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"failed to initialize mem0 AsyncMemory: {exc}") from exc

        try:
            await self._memory.get_all(agent_id=self._ikaros_agent_id)
        except Exception as exc:
            raise RuntimeError(f"failed to verify mem0 provider: {exc}") from exc

    def _require_memory(self) -> Any:
        if self._memory is None:
            raise RuntimeError("mem0 provider is not initialized")
        return self._memory

    async def list_user_items(self, user_id: str) -> list[MemoryItem]:
        payload = await self._require_memory().get_all(user_id=str(user_id))
        return self._normalize_items(payload, parse_ikaros_day=False)

    async def add_user_items(
        self, user_id: str, items: list[MemoryItem]
    ) -> list[MemoryItem]:
        memory = self._require_memory()
        added: list[MemoryItem] = []
        for item in items:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            await memory.add(
                messages=[{"role": "user", "content": text}],
                user_id=str(user_id),
            )
            added.append({"text": text, "metadata": {}, "created_at": ""})
        return added

    async def list_ikaros_items(self) -> list[MemoryItem]:
        payload = await self._require_memory().get_all(agent_id=self._ikaros_agent_id)
        return self._normalize_items(payload, parse_ikaros_day=True)

    async def add_ikaros_items(self, items: list[MemoryItem]) -> list[MemoryItem]:
        memory = self._require_memory()
        added: list[MemoryItem] = []
        for item in items:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            metadata = item.get("metadata") if isinstance(item, dict) else {}
            day = ""
            if isinstance(metadata, dict):
                day = str(metadata.get("day") or "").strip()
            content = f"[{day}] {text}" if day else text
            await memory.add(
                messages=[{"role": "assistant", "content": content}],
                agent_id=self._ikaros_agent_id,
            )
            normalized: MemoryItem = {"text": text, "metadata": {}, "created_at": ""}
            if day:
                normalized["metadata"] = {"day": day}
            added.append(normalized)
        return added

    @staticmethod
    def _extract_rows(payload: Any) -> list[Any]:
        if isinstance(payload, dict):
            for key in ("results", "items", "data", "memories"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    return candidate
        if isinstance(payload, list):
            return payload
        return []

    @staticmethod
    def _extract_text(row: Any) -> str:
        if isinstance(row, str):
            return row.strip()
        if not isinstance(row, dict):
            return ""
        for key in ("memory", "text", "data", "value"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        message = row.get("message")
        if isinstance(message, dict):
            for key in ("content", "text"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @classmethod
    def _normalize_items(
        cls, payload: Any, *, parse_ikaros_day: bool
    ) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        for row in cls._extract_rows(payload):
            text = cls._extract_text(row)
            if not text:
                continue
            metadata: dict[str, Any] = {}
            if parse_ikaros_day:
                match = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]\s*(.+)$", text)
                if match:
                    metadata["day"] = match.group(1)
                    text = match.group(2).strip()
            created_at = ""
            if isinstance(row, dict):
                created_at = str(
                    row.get("created_at") or row.get("updated_at") or ""
                ).strip()
            items.append(
                {
                    "text": text,
                    "metadata": metadata,
                    "created_at": created_at,
                }
            )
        items.sort(
            key=lambda item: (
                str(item.get("metadata", {}).get("day") or ""),
                str(item.get("created_at") or ""),
                str(item.get("text") or ""),
            )
        )
        return items


class Mem0MemoryExtension(MemoryExtension):
    name = "mem0_memory"
    provider_name = "mem0"
    priority = 20

    def enabled(self, runtime) -> bool:
        return get_memory_provider_name() == self.provider_name

    def create_provider(self, runtime):
        config = load_memory_config()
        return Mem0LongTermMemoryProvider(config.get_provider_settings(self.provider_name))
