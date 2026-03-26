from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, List

from core.audit_store import audit_store
from core.config import get_client_for_model
from core.model_config import get_current_model
from core.state_paths import system_path, user_path


logger = logging.getLogger(__name__)

# Backward-compatible async client injection for tests/legacy callers.
openai_async_client: Any = None

MEMORY_EXTRACTION_MAX_INPUT_CHARS = int(
    os.getenv("MEMORY_EXTRACTION_MAX_INPUT_CHARS", "12000")
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_key(value: str, fallback: str = "unknown") -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    safe = re.sub(r"[^a-zA-Z0-9_\-:.]+", "_", raw)
    return safe or fallback


def _norm_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    lowered = re.sub(r"[\s\r\n\t]+", "", lowered)
    lowered = re.sub(r"[，。！？,.!?;:：、\"'`~()（）\[\]{}<>]", "", lowered)
    return lowered[:240]


def _resolve_memory_client(model_name: str) -> Any:
    if openai_async_client is not None:
        return openai_async_client
    return get_client_for_model(model_name, is_async=True)


def _extract_response_text(response: Any) -> str:
    choices = list(getattr(response, "choices", []) or [])
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
    direct_text = getattr(response, "text", None)
    if isinstance(direct_text, str):
        return direct_text.strip()
    return ""


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    candidates.extend(re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.I))
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except Exception:
            continue
        if isinstance(loaded, dict):
            return loaded
    return {}


class MarkdownMemoryStore:
    """Per-user markdown memory store: MEMORY.md + memory/YYYY-MM-DD.md."""

    MIGRATION_MARK = "<!-- migrated-from-memory-json -->"

    def __init__(self):
        self.users_root = user_path("private")
        self.users_root.mkdir(parents=True, exist_ok=True)

    def _user_root(self, user_id: str) -> Path:
        return user_path(user_id).resolve()

    def _system_root(self) -> Path:
        return system_path()

    def memory_path(self, user_id: str) -> Path:
        return (self._user_root(user_id) / "MEMORY.md").resolve()

    def daily_dir(self, user_id: str) -> Path:
        return (self._user_root(user_id) / "memory").resolve()

    def daily_path(self, user_id: str, day: date | None = None) -> Path:
        target_day = day or date.today()
        return (self.daily_dir(user_id) / f"{target_day.isoformat()}.md").resolve()

    def ikaros_memory_path(self) -> Path:
        return (self._system_root() / "IKAROS_MEMORY.md").resolve()

    def ikaros_daily_path(self, day: date | None = None) -> Path:
        target_day = day or date.today()
        return (
            self._system_root() / "ikaros_memory" / f"{target_day.isoformat()}.md"
        ).resolve()

    def _rollup_marker_path(self, user_id: str, day: date) -> Path:
        return (self.daily_dir(user_id) / f".rollup-{day.isoformat()}.done").resolve()

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            return ""
        return ""

    @staticmethod
    def _dedupe(items: List[str], limit: int = 200) -> List[str]:
        rows: List[str] = []
        seen: set[str] = set()
        for raw in items:
            text = str(raw or "").strip()
            if not text:
                continue
            key = _norm_text(text)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(text)
            if len(rows) >= limit:
                break
        return rows

    def _write_text(self, path: Path, content: str, *, actor: str, reason: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        audit_store.write_versioned(
            path,
            content if content.endswith("\n") else content + "\n",
            actor=actor,
            reason=reason,
            category="memory",
        )

    def _ensure_ikaros_memory_file(self) -> None:
        path = self.ikaros_memory_path()
        if path.exists() and self._read_text(path).strip():
            return
        content = "# MANAGER MEMORY\n\n## 经验记忆\n\n"
        try:
            self._write_text(
                path, content, actor="system", reason="init_ikaros_memory"
            )
        except Exception:
            return

    async def initialize(self) -> None:
        self._ensure_ikaros_memory_file()

    def list_user_items_sync(self, user_id: str) -> List[dict[str, Any]]:
        self.ensure_migrated(user_id)
        items: List[dict[str, Any]] = []
        for line in self._read_text(self.memory_path(user_id)).splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            text = stripped[2:].strip()
            if not text:
                continue
            items.append({"text": text, "metadata": {}, "created_at": ""})
        return items

    async def list_user_items(self, user_id: str) -> List[dict[str, Any]]:
        return self.list_user_items_sync(user_id)

    async def add_user_items(
        self, user_id: str, items: List[dict[str, Any]]
    ) -> List[dict[str, Any]]:
        self.ensure_migrated(user_id)
        memory_path = self.memory_path(user_id)
        current_memory = self._read_text(memory_path)
        cleaned = [
            str(item.get("text") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        if not cleaned:
            return []

        if not current_memory.strip():
            current_memory = "# MEMORY\n\n## 用户长期记忆\n\n"
        elif "## 用户长期记忆" not in current_memory:
            current_memory = current_memory.rstrip() + "\n\n## 用户长期记忆\n\n"
        elif not current_memory.endswith("\n"):
            current_memory += "\n"

        merged = current_memory.rstrip() + "\n"
        for text in cleaned:
            merged += f"- {text}\n"
        self._write_text(
            memory_path,
            merged,
            actor="system",
            reason="provider_add_user_memory",
        )
        return [{"text": text, "metadata": {}, "created_at": ""} for text in cleaned]

    def list_ikaros_items_sync(self) -> List[dict[str, Any]]:
        self._ensure_ikaros_memory_file()
        items: List[dict[str, Any]] = []
        for line in self._read_text(self.ikaros_memory_path()).splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            payload = stripped[2:].strip()
            if not payload:
                continue
            metadata: dict[str, Any] = {}
            text = payload
            matched = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]\s*(.+)$", payload)
            if matched:
                metadata["day"] = matched.group(1)
                text = matched.group(2).strip()
            items.append({"text": text, "metadata": metadata, "created_at": ""})
        return items

    async def list_ikaros_items(self) -> List[dict[str, Any]]:
        return self.list_ikaros_items_sync()

    async def add_ikaros_items(
        self, items: List[dict[str, Any]]
    ) -> List[dict[str, Any]]:
        self._ensure_ikaros_memory_file()
        path = self.ikaros_memory_path()
        current = self._read_text(path)
        cleaned: List[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            cleaned.append(
                {
                    "text": text,
                    "metadata": dict(metadata),
                    "created_at": str(item.get("created_at") or "").strip(),
                }
            )
        if not cleaned:
            return []

        if "## 经验记忆" not in current:
            current = current.rstrip() + "\n\n## 经验记忆\n\n"
        merged = current.rstrip() + "\n"
        for item in cleaned:
            day = str(item.get("metadata", {}).get("day") or "").strip()
            text = str(item.get("text") or "").strip()
            if day:
                merged += f"- [{day}] {text}\n"
            else:
                merged += f"- {text}\n"
        self._write_text(
            path,
            merged,
            actor="system",
            reason="provider_add_ikaros_memory",
        )
        return cleaned

    def _parse_legacy_memory_json(self, path: Path) -> List[str]:
        lines: List[str] = []
        if not path.exists():
            return lines

        entity_by_name: dict[str, dict] = {}
        relations: List[dict] = []
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                text = raw.strip()
                if not text:
                    continue
                with_recovered = text
                try:
                    payload = json.loads(with_recovered)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                kind = str(payload.get("type") or "").strip().lower()
                if kind == "entity":
                    name = str(payload.get("name") or "").strip()
                    if name:
                        entity_by_name[name] = payload
                elif kind == "relation":
                    relations.append(payload)
        except Exception:
            return []

        user = entity_by_name.get("User")
        if isinstance(user, dict):
            observations = user.get("observations")
            if isinstance(observations, list):
                for item in observations:
                    value = str(item or "").strip()
                    if value and value != "当前交互用户":
                        lines.append(value)

        for rel in relations:
            if not isinstance(rel, dict):
                continue
            from_name = str(rel.get("from") or "").strip()
            to_name = str(rel.get("to") or "").strip()
            relation_type = str(rel.get("relationType") or "").strip().lower()
            if from_name != "User" or not to_name:
                continue
            if relation_type == "lives in":
                lines.append(f"居住地：{to_name}")
            else:
                lines.append(f"关系：{relation_type or 'related to'} {to_name}")

            target = entity_by_name.get(to_name)
            if isinstance(target, dict):
                target_obs = target.get("observations")
                if isinstance(target_obs, list):
                    for obs in target_obs[:2]:
                        text = str(obs or "").strip()
                        if text:
                            lines.append(f"{to_name}：{text}")

        return self._dedupe(lines)

    def ensure_migrated(self, user_id: str) -> None:
        user_root = self._user_root(user_id)
        user_root.mkdir(parents=True, exist_ok=True)

        memory_path = self.memory_path(user_id)
        legacy_path = (user_root / "memory.json").resolve()

        current = self._read_text(memory_path)
        if current and self.MIGRATION_MARK in current:
            return

        if not legacy_path.exists():
            if not current.strip():
                self._write_text(
                    memory_path,
                    "# MEMORY\n\n## 用户长期记忆\n\n",
                    actor="system",
                    reason="init_memory_markdown",
                )
            return

        if current.strip():
            return

        migrated_items = self._parse_legacy_memory_json(legacy_path)
        lines: List[str] = ["# MEMORY", "", "## 用户长期记忆", ""]
        if migrated_items:
            lines.extend([f"- {item}" for item in migrated_items])
            lines.append("")
        lines.extend([self.MIGRATION_MARK, f"- migrated_at: {_now_iso()}", ""])
        self._write_text(
            memory_path,
            "\n".join(lines),
            actor="system",
            reason="migrate_legacy_memory_json",
        )

    @staticmethod
    def _extract_memory_facts(raw: str) -> List[str]:
        facts = MarkdownMemoryStore._split_sentences(str(raw or ""))
        if not facts and str(raw or "").strip():
            facts = [str(raw or "").strip()]
        return MarkdownMemoryStore._dedupe(facts, limit=32)

    def remember(
        self, user_id: str, content: str, *, source: str = "chat"
    ) -> tuple[bool, str]:
        text = str(content or "").strip()
        if not text:
            return False, "内容为空"

        self.ensure_migrated(user_id)
        memory_path = self.memory_path(user_id)
        daily_path = self.daily_path(user_id)

        current_memory = self._read_text(memory_path)
        facts = self._extract_memory_facts(text)
        if not facts:
            facts = [text]

        existing_norms: set[str] = set()
        for line in current_memory.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                existing_norms.add(_norm_text(stripped[2:]))

        new_facts = [item for item in facts if _norm_text(item) not in existing_norms]

        if not current_memory.strip():
            current_memory = "# MEMORY\n\n## 用户长期记忆\n\n"
        elif "## 用户长期记忆" not in current_memory:
            current_memory = current_memory.rstrip() + "\n\n## 用户长期记忆\n\n"
        elif not current_memory.endswith("\n"):
            current_memory += "\n"

        if new_facts:
            memory_content = current_memory.rstrip() + "\n"
            for item in new_facts:
                memory_content += f"- {item}\n"
            self._write_text(
                memory_path,
                memory_content,
                actor="user",
                reason="remember_user_memory",
            )

        daily_existing = self._read_text(daily_path)
        header = f"# {date.today().isoformat()}\n\n"
        if not daily_existing.strip():
            daily_existing = header
        elif not daily_existing.endswith("\n"):
            daily_existing += "\n"

        stamp = datetime.now().strftime("%H:%M:%S")
        daily_line = f"- [{stamp}] source={source}: {text[:500]}"
        daily_content = daily_existing.rstrip() + f"\n{daily_line}\n"
        self._write_text(
            daily_path,
            daily_content,
            actor="user",
            reason="append_daily_memory",
        )

        detail = "；".join((new_facts or facts)[:4])
        return True, detail

    def remember_facts(
        self,
        user_id: str,
        facts: List[str],
        *,
        source: str = "daily_rollup",
    ) -> int:
        cleaned = self._dedupe([str(item or "").strip() for item in facts], limit=16)
        if not cleaned:
            return 0

        self.ensure_migrated(user_id)
        memory_path = self.memory_path(user_id)
        current_memory = self._read_text(memory_path)
        if not current_memory.strip():
            current_memory = "# MEMORY\n\n## 用户长期记忆\n\n"
        elif "## 用户长期记忆" not in current_memory:
            current_memory = current_memory.rstrip() + "\n\n## 用户长期记忆\n\n"

        existing_norms: set[str] = set()
        for line in current_memory.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                existing_norms.add(_norm_text(stripped[2:]))

        added: list[str] = []
        for fact in cleaned:
            key = _norm_text(fact)
            if not key or key in existing_norms:
                continue
            existing_norms.add(key)
            added.append(fact)

        if not added:
            return 0

        merged = current_memory.rstrip() + "\n"
        for fact in added:
            merged += f"- {fact}\n"
        self._write_text(
            memory_path, merged, actor="system", reason="daily_rollup_user_memory"
        )

        # Append concise daily trace for observability.
        day_path = self.daily_path(user_id)
        existing_daily = self._read_text(day_path)
        if not existing_daily.strip():
            existing_daily = f"# {date.today().isoformat()}\n\n"
        line = (
            f"- [{datetime.now().strftime('%H:%M:%S')}] source={source}: "
            + "；".join(added[:6])
        )
        self._write_text(
            day_path,
            existing_daily.rstrip() + f"\n{line}\n",
            actor="system",
            reason="daily_rollup_trace",
        )
        return len(added)

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        pieces = re.split(r"[\n。！？!?；;]+", str(text or ""))
        return [str(item or "").strip() for item in pieces if str(item or "").strip()]

    @staticmethod
    def _render_transcripts_for_ai(
        transcripts: List[dict[str, Any]],
        *,
        max_chars: int = MEMORY_EXTRACTION_MAX_INPUT_CHARS,
    ) -> str:
        lines: list[str] = []
        total_chars = 0
        for bundle_index, bundle in enumerate(transcripts, start=1):
            messages = bundle.get("messages") if isinstance(bundle, dict) else None
            if not isinstance(messages, list):
                continue
            header = f"## Session {bundle_index}"
            lines.append(header)
            total_chars += len(header) + 1
            for item in messages:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip().lower()
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                role_label = "user" if role == "user" else "assistant"
                line = f"{role_label}: {content[:800]}"
                lines.append(line)
                total_chars += len(line) + 1
                if total_chars >= max_chars:
                    break
            if total_chars >= max_chars:
                break
        rendered = "\n".join(lines).strip()
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars]
        return rendered

    async def extract_user_facts_ai(
        self,
        text: str,
        *,
        max_facts: int = 8,
    ) -> List[str]:
        request = str(text or "").strip()
        if not request:
            return []

        model_to_use = get_current_model()
        client = _resolve_memory_client(model_to_use)
        if client is None:
            return []

        messages = [
            {
                "role": "system",
                "content": (
                    "You extract durable user memory facts. "
                    "Return JSON only with key `facts` (array of strings). "
                    "Only keep explicit long-term user facts or explicit remember requests. "
                    "Do not infer. Do not keep temporary tasks or one-off requests. "
                    "Normalize concise facts in Chinese when possible, for example "
                    "`偏好称呼：老王`, `居住地：北京`, `身份：后端工程师`."
                ),
            },
            {
                "role": "user",
                "content": f"user_text:\n{request[:MEMORY_EXTRACTION_MAX_INPUT_CHARS]}",
            },
        ]
        request_kwargs: dict[str, Any] = {
            "model": model_to_use,
            "messages": messages,
            "temperature": 0,
        }
        try:
            response = await client.chat.completions.create(
                **request_kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            try:
                response = await client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                logger.debug("User memory extraction failed: %s", exc)
                return []

        payload = _extract_json_object(_extract_response_text(response))
        raw_facts = payload.get("facts")
        if not isinstance(raw_facts, list):
            return []
        return self._dedupe(
            [str(item or "").strip() for item in raw_facts],
            limit=max_facts,
        )

    async def extract_daily_rollup_ai(
        self,
        transcripts: List[dict[str, Any]],
    ) -> dict[str, List[str]]:
        rendered = self._render_transcripts_for_ai(transcripts)
        if not rendered:
            return {"user_facts": [], "ikaros_experiences": []}

        model_to_use = get_current_model()
        client = _resolve_memory_client(model_to_use)
        if client is None:
            return {"user_facts": [], "ikaros_experiences": []}

        messages = [
            {
                "role": "system",
                "content": (
                    "You extract structured long-term memory from conversation transcripts. "
                    "Return JSON only with keys `user_facts` and `ikaros_experiences`, both arrays of strings. "
                    "`user_facts` should contain only durable user facts explicitly stated by the user and useful for future personalization. "
                    "`ikaros_experiences` should contain only reusable operator or engineering lessons from assistant outputs, not user-specific wording. "
                    "Do not infer. Do not keep temporary tasks, transient requests, or duplicates. "
                    "Keep each item concise."
                ),
            },
            {
                "role": "user",
                "content": f"transcripts:\n{rendered}",
            },
        ]
        request_kwargs: dict[str, Any] = {
            "model": model_to_use,
            "messages": messages,
            "temperature": 0,
        }
        try:
            response = await client.chat.completions.create(
                **request_kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            try:
                response = await client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                logger.debug("Daily memory rollup extraction failed: %s", exc)
                return {"user_facts": [], "ikaros_experiences": []}

        payload = _extract_json_object(_extract_response_text(response))
        user_facts = payload.get("user_facts")
        ikaros_experiences = payload.get("ikaros_experiences")
        return {
            "user_facts": self._dedupe(
                [str(item or "").strip() for item in user_facts]
                if isinstance(user_facts, list)
                else [],
                limit=6,
            ),
            "ikaros_experiences": self._dedupe(
                [str(item or "").strip() for item in ikaros_experiences]
                if isinstance(ikaros_experiences, list)
                else [],
                limit=5,
            ),
        }

    def add_ikaros_experiences(
        self,
        experiences: List[str],
        *,
        day: date,
        source_user_id: str,
    ) -> int:
        records = self._dedupe(
            [str(item or "").strip() for item in experiences], limit=8
        )
        if not records:
            return 0

        self._ensure_ikaros_memory_file()
        path = self.ikaros_memory_path()
        current = self._read_text(path)
        if not current and not path.exists():
            return 0
        existing_norms: set[str] = set()
        for line in current.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                existing_norms.add(_norm_text(stripped[2:]))

        added: list[str] = []
        for item in records:
            normalized = _norm_text(item)
            if not normalized or normalized in existing_norms:
                continue
            existing_norms.add(normalized)
            added.append(item)

        if not added:
            return 0

        if "## 经验记忆" not in current:
            current = current.rstrip() + "\n\n## 经验记忆\n\n"
        merged = current.rstrip() + "\n"
        for item in added:
            merged += f"- [{day.isoformat()}] {item}\n"
        try:
            self._write_text(
                path, merged, actor="system", reason="daily_rollup_ikaros_memory"
            )
        except Exception:
            return 0

        day_path = self.ikaros_daily_path(day)
        daily_existing = self._read_text(day_path)
        if not daily_existing.strip():
            daily_existing = f"# {day.isoformat()}\n\n"
        detail = f"- user={source_user_id}: " + "；".join(added[:5])
        try:
            self._write_text(
                day_path,
                daily_existing.rstrip() + f"\n{detail}\n",
                actor="system",
                reason="daily_rollup_ikaros_daily",
            )
        except Exception:
            pass
        return len(added)

    def load_ikaros_snapshot(self, *, max_chars: int = 1600) -> str:
        content = self._read_text(self.ikaros_memory_path()).strip()
        if not content:
            return ""
        if len(content) <= max_chars:
            return content
        return content[-max_chars:]

    async def rollup_today_sessions(
        self,
        user_id: str,
        *,
        target_day: date | None = None,
    ) -> dict[str, Any]:
        """Daily memory rollup: extract high-value user memory + ikaros experience."""
        day = target_day or date.today()
        marker = self._rollup_marker_path(user_id, day)
        if marker.exists():
            return {"ok": True, "skipped": True, "reason": "already_rolled"}

        from core.state_store import get_day_session_transcripts

        transcripts = await get_day_session_transcripts(
            user_id=str(user_id),
            day=day,
            max_sessions=32,
            max_chars_per_session=5000,
        )

        extracted = await self.extract_daily_rollup_ai(transcripts)
        user_facts = self._dedupe(extracted.get("user_facts") or [], limit=6)
        added_user_count = self.remember_facts(
            str(user_id),
            user_facts,
            source="daily_session_rollup",
        )

        ikaros_experiences = self._dedupe(
            extracted.get("ikaros_experiences") or [],
            limit=5,
        )
        added_ikaros_count = self.add_ikaros_experiences(
            ikaros_experiences,
            day=day,
            source_user_id=str(user_id),
        )

        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker.write_text(
                f"rolled_at: {_now_iso()}\nuser_memory_added: {added_user_count}\nikaros_exp_added: {added_ikaros_count}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

        return {
            "ok": True,
            "skipped": False,
            "user_memory_added": added_user_count,
            "ikaros_experience_added": added_ikaros_count,
            "sessions": len(transcripts),
        }

    def load_snapshot(
        self,
        user_id: str,
        *,
        include_daily: bool = True,
        max_chars: int = 2400,
    ) -> str:
        self.ensure_migrated(user_id)
        blocks: List[str] = []

        memory_content = self._read_text(self.memory_path(user_id)).strip()
        if memory_content:
            blocks.append(f"【长期记忆（MEMORY.md）】\n{memory_content}")

        if include_daily:
            today = date.today()
            for day in (today, today - timedelta(days=1)):
                daily_text = self._read_text(self.daily_path(user_id, day)).strip()
                if not daily_text:
                    continue
                lines = daily_text.splitlines()
                tail = "\n".join(lines[-20:]).strip()
                if tail:
                    blocks.append(f"【近期记忆（{day.isoformat()}）】\n{tail}")

        if not blocks:
            return ""

        snapshot = "\n\n".join(blocks).strip()
        if len(snapshot) <= max_chars:
            return snapshot
        return snapshot[-max_chars:]


markdown_memory_store = MarkdownMemoryStore()
