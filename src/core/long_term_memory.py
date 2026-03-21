from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from core.markdown_memory_store import _norm_text, _now_iso, markdown_memory_store
from core.memory_config import get_memory_provider_name, load_memory_config

logger = logging.getLogger(__name__)

MemoryItem = dict[str, Any]


class LongTermMemoryProvider(Protocol):
    async def initialize(self) -> None: ...

    async def list_user_items(self, user_id: str) -> list[MemoryItem]: ...

    async def add_user_items(
        self, user_id: str, items: list[MemoryItem]
    ) -> list[MemoryItem]: ...

    async def list_manager_items(self) -> list[MemoryItem]: ...

    async def add_manager_items(self, items: list[MemoryItem]) -> list[MemoryItem]: ...


class Mem0LongTermMemoryProvider:
    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = dict(settings or {})
        self._memory: Any = None
        self._manager_agent_id = "core-manager"

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
            await self._memory.get_all(agent_id=self._manager_agent_id)
        except Exception as exc:
            raise RuntimeError(f"failed to verify mem0 provider: {exc}") from exc

    def _require_memory(self) -> Any:
        if self._memory is None:
            raise RuntimeError("mem0 provider is not initialized")
        return self._memory

    async def list_user_items(self, user_id: str) -> list[MemoryItem]:
        payload = await self._require_memory().get_all(user_id=str(user_id))
        return self._normalize_items(payload, parse_manager_day=False)

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

    async def list_manager_items(self) -> list[MemoryItem]:
        payload = await self._require_memory().get_all(agent_id=self._manager_agent_id)
        return self._normalize_items(payload, parse_manager_day=True)

    async def add_manager_items(self, items: list[MemoryItem]) -> list[MemoryItem]:
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
                agent_id=self._manager_agent_id,
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
        cls, payload: Any, *, parse_manager_day: bool
    ) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        for row in cls._extract_rows(payload):
            text = cls._extract_text(row)
            if not text:
                continue
            metadata: dict[str, Any] = {}
            if parse_manager_day:
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


class LongTermMemoryService:
    def __init__(self):
        self._provider: LongTermMemoryProvider | None = None
        self._provider_name = ""
        self._init_lock: asyncio.Lock | None = None
        self._initialized = False
        self._manager_snapshot_cache = ""

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._initialized:
                return
            provider_name = get_memory_provider_name()
            provider = self._build_provider(provider_name)
            await provider.initialize()
            self._provider = provider
            self._provider_name = provider_name
            self._initialized = True
            if provider_name == "mem0":
                await self._refresh_manager_snapshot_cache()

    def get_provider_name(self) -> str:
        if self._provider_name:
            return self._provider_name
        return get_memory_provider_name()

    def _build_provider(self, provider_name: str) -> LongTermMemoryProvider:
        config = load_memory_config()
        name = str(provider_name or "").strip().lower()
        if not name:
            raise RuntimeError("long-term memory provider is empty")
        if name not in config.providers:
            raise RuntimeError(f"unknown long-term memory provider: {name}")
        if name == "file":
            return markdown_memory_store
        if name == "mem0":
            return Mem0LongTermMemoryProvider(config.get_provider_settings(name))
        raise RuntimeError(f"unsupported long-term memory provider: {name}")

    async def _get_provider(self) -> LongTermMemoryProvider:
        if not self._initialized:
            await self.initialize()
        if self._provider is None:
            raise RuntimeError("long-term memory provider is not initialized")
        return self._provider

    @staticmethod
    def _dedupe_items(items: list[MemoryItem], *, keep_day: bool = False) -> list[MemoryItem]:
        rows: list[MemoryItem] = []
        seen: set[str] = set()
        for item in items:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            metadata = item.get("metadata") if isinstance(item, dict) else {}
            day = ""
            if keep_day and isinstance(metadata, dict):
                day = str(metadata.get("day") or "").strip()
            key = _norm_text(f"{day}:{text}" if day else text)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "text": text,
                    "metadata": dict(metadata) if isinstance(metadata, dict) else {},
                    "created_at": str(item.get("created_at") or "").strip(),
                }
            )
        return rows

    @staticmethod
    def _truncate(text: str, *, max_chars: int) -> str:
        content = str(text or "").strip()
        if len(content) <= max_chars:
            return content
        return content[-max_chars:]

    def _render_manager_snapshot(self, items: list[MemoryItem], *, max_chars: int) -> str:
        lines: list[str] = []
        for item in self._dedupe_items(items):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            metadata = item.get("metadata") if isinstance(item, dict) else {}
            day = ""
            if isinstance(metadata, dict):
                day = str(metadata.get("day") or "").strip()
            if day:
                lines.append(f"- [{day}] {text}")
            else:
                lines.append(f"- {text}")
        return self._truncate("\n".join(lines).strip(), max_chars=max_chars)

    async def _refresh_manager_snapshot_cache(self) -> None:
        provider = await self._get_provider()
        items = await provider.list_manager_items()
        self._manager_snapshot_cache = self._render_manager_snapshot(
            items,
            max_chars=100000,
        )

    async def load_user_snapshot(
        self,
        user_id: str,
        *,
        include_daily: bool = True,
        max_chars: int = 2400,
    ) -> str:
        provider = await self._get_provider()
        items = self._dedupe_items(await provider.list_user_items(str(user_id)))

        blocks: list[str] = []
        if items:
            blocks.append(
                "【长期记忆】\n"
                + "\n".join(
                    f"- {str(item.get('text') or '').strip()}"
                    for item in items
                    if str(item.get("text") or "").strip()
                )
            )

        if include_daily:
            today = date.today()
            for day in (today, today - timedelta(days=1)):
                daily_text = markdown_memory_store._read_text(
                    markdown_memory_store.daily_path(str(user_id), day)
                ).strip()
                if not daily_text:
                    continue
                lines = daily_text.splitlines()
                tail = "\n".join(lines[-20:]).strip()
                if tail:
                    blocks.append(f"【近期记忆（{day.isoformat()}）】\n{tail}")

        if not blocks:
            return ""
        return self._truncate("\n\n".join(blocks).strip(), max_chars=max_chars)

    async def remember_user(
        self, user_id: str, content: str, *, source: str = "chat"
    ) -> tuple[bool, str]:
        text = str(content or "").strip()
        if not text:
            return False, "内容为空"

        provider = await self._get_provider()
        existing_items = await provider.list_user_items(str(user_id))
        existing_norms = {
            _norm_text(str(item.get("text") or "").strip()) for item in existing_items
        }

        facts = markdown_memory_store._extract_memory_facts(text)
        if not facts:
            facts = [text]
        new_facts = [item for item in facts if _norm_text(item) not in existing_norms]

        if new_facts:
            await provider.add_user_items(
                str(user_id),
                [{"text": item, "metadata": {}, "created_at": ""} for item in new_facts],
            )

        daily_path = markdown_memory_store.daily_path(str(user_id))
        daily_existing = markdown_memory_store._read_text(daily_path)
        header = f"# {date.today().isoformat()}\n\n"
        if not daily_existing.strip():
            daily_existing = header
        elif not daily_existing.endswith("\n"):
            daily_existing += "\n"

        stamp = datetime.now().strftime("%H:%M:%S")
        daily_line = f"- [{stamp}] source={source}: {text[:500]}"
        markdown_memory_store._write_text(
            daily_path,
            daily_existing.rstrip() + f"\n{daily_line}\n",
            actor="user",
            reason="append_daily_memory",
        )

        detail = "；".join((new_facts or facts)[:4])
        return True, detail

    async def remember_user_facts(
        self,
        user_id: str,
        facts: list[str],
        *,
        source: str = "daily_rollup",
    ) -> int:
        cleaned = markdown_memory_store._dedupe(
            [str(item or "").strip() for item in facts],
            limit=16,
        )
        if not cleaned:
            return 0

        provider = await self._get_provider()
        existing_items = await provider.list_user_items(str(user_id))
        existing_norms = {
            _norm_text(str(item.get("text") or "").strip()) for item in existing_items
        }
        added = [fact for fact in cleaned if _norm_text(fact) not in existing_norms]
        if not added:
            return 0

        await provider.add_user_items(
            str(user_id),
            [{"text": fact, "metadata": {}, "created_at": ""} for fact in added],
        )

        day_path = markdown_memory_store.daily_path(str(user_id))
        existing_daily = markdown_memory_store._read_text(day_path)
        if not existing_daily.strip():
            existing_daily = f"# {date.today().isoformat()}\n\n"
        line = (
            f"- [{datetime.now().strftime('%H:%M:%S')}] source={source}: "
            + "；".join(added[:6])
        )
        markdown_memory_store._write_text(
            day_path,
            existing_daily.rstrip() + f"\n{line}\n",
            actor="system",
            reason="daily_rollup_trace",
        )
        return len(added)

    def load_manager_snapshot(self, *, max_chars: int = 1600) -> str:
        provider_name = self.get_provider_name()
        if provider_name == "file":
            return self._render_manager_snapshot(
                markdown_memory_store.list_manager_items_sync(),
                max_chars=max_chars,
            )
        if not self._initialized:
            raise RuntimeError(
                f"long-term memory provider '{provider_name}' must be initialized before sync access"
            )
        return self._truncate(self._manager_snapshot_cache, max_chars=max_chars)

    async def add_manager_experiences(
        self,
        experiences: list[str],
        *,
        day: date,
        source_user_id: str,
    ) -> int:
        records = markdown_memory_store._dedupe(
            [str(item or "").strip() for item in experiences],
            limit=8,
        )
        if not records:
            return 0

        provider = await self._get_provider()
        existing_items = await provider.list_manager_items()
        existing_norms = {
            _norm_text(str(item.get("text") or "").strip()) for item in existing_items
        }
        added = [item for item in records if _norm_text(item) not in existing_norms]
        if not added:
            return 0

        await provider.add_manager_items(
            [
                {
                    "text": item,
                    "metadata": {"day": day.isoformat()},
                    "created_at": "",
                }
                for item in added
            ]
        )

        day_path = markdown_memory_store.manager_daily_path(day)
        daily_existing = markdown_memory_store._read_text(day_path)
        if not daily_existing.strip():
            daily_existing = f"# {day.isoformat()}\n\n"
        detail = f"- user={source_user_id}: " + "；".join(added[:5])
        markdown_memory_store._write_text(
            day_path,
            daily_existing.rstrip() + f"\n{detail}\n",
            actor="system",
            reason="daily_rollup_manager_daily",
        )

        if self.get_provider_name() == "mem0":
            await self._refresh_manager_snapshot_cache()
        return len(added)

    async def rollup_today_sessions(
        self,
        user_id: str,
        *,
        target_day: date | None = None,
    ) -> dict[str, Any]:
        day = target_day or date.today()
        marker = markdown_memory_store._rollup_marker_path(str(user_id), day)
        if marker.exists():
            return {"ok": True, "skipped": True, "reason": "already_rolled"}

        from core.state_store import get_day_session_transcripts

        transcripts = await get_day_session_transcripts(
            user_id=str(user_id),
            day=day,
            max_sessions=32,
            max_chars_per_session=5000,
        )

        user_facts: list[str] = []
        for bundle in transcripts:
            messages = bundle.get("messages") if isinstance(bundle, dict) else None
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip().lower()
                if role != "user":
                    continue
                extracted = markdown_memory_store._extract_memory_facts(
                    str(item.get("content") or "")
                )
                for fact in extracted:
                    if markdown_memory_store._is_high_value_user_fact(fact):
                        user_facts.append(fact)

        user_facts = markdown_memory_store._dedupe(user_facts, limit=6)
        added_user_count = await self.remember_user_facts(
            str(user_id),
            user_facts,
            source="daily_session_rollup",
        )

        manager_experiences = markdown_memory_store._extract_manager_experiences(
            transcripts
        )
        added_manager_count = await self.add_manager_experiences(
            manager_experiences,
            day=day,
            source_user_id=str(user_id),
        )

        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker.write_text(
                (
                    f"rolled_at: {_now_iso()}\n"
                    f"user_memory_added: {added_user_count}\n"
                    f"manager_exp_added: {added_manager_count}\n"
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        return {
            "ok": True,
            "skipped": False,
            "user_memory_added": added_user_count,
            "manager_experience_added": added_manager_count,
            "sessions": len(transcripts),
        }


long_term_memory = LongTermMemoryService()
