from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, List

from core.audit_store import audit_store
from core.config import DATA_DIR


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


class MarkdownMemoryStore:
    """Per-user markdown memory store: MEMORY.md + memory/YYYY-MM-DD.md."""

    MIGRATION_MARK = "<!-- migrated-from-memory-json -->"

    def __init__(self):
        self.users_root = (Path(DATA_DIR) / "users").resolve()
        self.users_root.mkdir(parents=True, exist_ok=True)

    def _user_root(self, user_id: str) -> Path:
        return (self.users_root / _safe_key(user_id)).resolve()

    def _system_root(self) -> Path:
        root = (Path(DATA_DIR) / "system").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def memory_path(self, user_id: str) -> Path:
        return (self._user_root(user_id) / "MEMORY.md").resolve()

    def daily_dir(self, user_id: str) -> Path:
        return (self._user_root(user_id) / "memory").resolve()

    def daily_path(self, user_id: str, day: date | None = None) -> Path:
        target_day = day or date.today()
        return (self.daily_dir(user_id) / f"{target_day.isoformat()}.md").resolve()

    def manager_memory_path(self) -> Path:
        return (self._system_root() / "MANAGER_MEMORY.md").resolve()

    def manager_daily_path(self, day: date | None = None) -> Path:
        target_day = day or date.today()
        return (
            self._system_root() / "manager_memory" / f"{target_day.isoformat()}.md"
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

    def _ensure_manager_memory_file(self) -> None:
        path = self.manager_memory_path()
        if path.exists() and self._read_text(path).strip():
            return
        content = "# MANAGER MEMORY\n\n## 经验记忆\n\n"
        try:
            self._write_text(
                path, content, actor="system", reason="init_manager_memory"
            )
        except Exception:
            return

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
        items = re.split(r"[，,。；;！!？?\n]+", str(raw or ""))
        facts: List[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue

            nickname_match = re.search(
                r"(?:以后)?(?:请)?(?:称呼我为|叫我|喊我)([^，,。；;]+)",
                text,
                flags=re.IGNORECASE,
            )
            if nickname_match:
                nickname = str(nickname_match.group(1) or "").strip()
                if nickname:
                    facts.append(f"偏好称呼：{nickname}")
                continue

            location_match = re.search(
                r"(?:我)?(?:住在|居住在|常住)([^，,。；;]+)",
                text,
                flags=re.IGNORECASE,
            )
            if location_match:
                location = str(location_match.group(1) or "").strip()
                if location:
                    facts.append(f"居住地：{location}")
                continue

            identity_match = re.search(
                r"(?:我(?:是|是一名|是个))([^，,。；;]+)",
                text,
                flags=re.IGNORECASE,
            )
            if identity_match:
                identity = str(identity_match.group(1) or "").strip()
                if identity:
                    facts.append(f"身份：{identity}")
                continue

            facts.append(text)

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
    def _is_high_value_user_fact(text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if raw.startswith(("偏好称呼：", "居住地：", "身份：")):
            return True
        if len(raw) < 4 or len(raw) > 100:
            return False
        keywords = (
            "喜欢",
            "不喜欢",
            "习惯",
            "偏好",
            "目标",
            "计划",
            "常用",
            "时区",
            "工作",
            "职业",
            "家庭",
        )
        return any(key in raw for key in keywords)

    @staticmethod
    def _extract_manager_experiences(transcripts: List[dict[str, Any]]) -> List[str]:
        cues = (
            "优先",
            "避免",
            "建议",
            "需要",
            "必须",
            "不要",
            "排查",
            "修复",
            "迁移",
            "兼容",
            "验证",
            "回滚",
            "失败",
            "成功",
            "稳定",
        )
        candidates: list[tuple[int, str]] = []
        for bundle in transcripts:
            messages = bundle.get("messages") if isinstance(bundle, dict) else None
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip().lower()
                if role != "model":
                    continue
                for sentence in MarkdownMemoryStore._split_sentences(
                    item.get("content", "")
                ):
                    text = sentence.strip()
                    if len(text) < 10 or len(text) > 120:
                        continue
                    if any(token in text for token in ("我住", "我喜欢", "你的", "您")):
                        continue
                    score = 0
                    score += sum(1 for cue in cues if cue in text)
                    if score <= 0:
                        continue
                    candidates.append((score, text))

        candidates.sort(key=lambda item: item[0], reverse=True)
        deduped: List[str] = []
        seen: set[str] = set()
        for _score, text in candidates:
            key = _norm_text(text)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(text)
            if len(deduped) >= 5:
                break
        return deduped

    def add_manager_experiences(
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

        self._ensure_manager_memory_file()
        path = self.manager_memory_path()
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
                path, merged, actor="system", reason="daily_rollup_manager_memory"
            )
        except Exception:
            return 0

        day_path = self.manager_daily_path(day)
        daily_existing = self._read_text(day_path)
        if not daily_existing.strip():
            daily_existing = f"# {day.isoformat()}\n\n"
        detail = f"- user={source_user_id}: " + "；".join(added[:5])
        try:
            self._write_text(
                day_path,
                daily_existing.rstrip() + f"\n{detail}\n",
                actor="system",
                reason="daily_rollup_manager_daily",
            )
        except Exception:
            pass
        return len(added)

    def load_manager_snapshot(self, *, max_chars: int = 1600) -> str:
        content = self._read_text(self.manager_memory_path()).strip()
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
        """Daily memory rollup: extract high-value user memory + manager experience."""
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

        user_facts: List[str] = []
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
                extracted = self._extract_memory_facts(str(item.get("content") or ""))
                for fact in extracted:
                    if self._is_high_value_user_fact(fact):
                        user_facts.append(fact)

        user_facts = self._dedupe(user_facts, limit=6)
        added_user_count = self.remember_facts(
            str(user_id),
            user_facts,
            source="daily_session_rollup",
        )

        manager_experiences = self._extract_manager_experiences(transcripts)
        added_manager_count = self.add_manager_experiences(
            manager_experiences,
            day=day,
            source_user_id=str(user_id),
        )

        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker.write_text(
                f"rolled_at: {_now_iso()}\nuser_memory_added: {added_user_count}\nmanager_exp_added: {added_manager_count}\n",
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
