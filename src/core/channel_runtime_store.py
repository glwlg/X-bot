from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from core.config import DATA_DIR


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ChannelRuntimeStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._ensure_file()

    @property
    def path(self) -> Path:
        return (
            Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()
            / "system"
            / "channel_runtime.json"
        ).resolve()

    @staticmethod
    def _safe_text(value: Any, limit: int = 0) -> str:
        rendered = str(value or "").strip()
        if limit > 0:
            return rendered[:limit]
        return rendered

    def _default_payload(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "aliases": {},
            "states": {},
        }

    def _ensure_file(self) -> None:
        with self._lock:
            if self.path.exists():
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write_unlocked(self._default_payload())

    def _read_unlocked(self) -> Dict[str, Any]:
        default = self._default_payload()
        if not self.path.exists():
            return default
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            loaded = {}
        if not isinstance(loaded, dict):
            return default
        merged = dict(default)
        merged.update(loaded)
        aliases = merged.get("aliases")
        states = merged.get("states")
        merged["aliases"] = dict(aliases) if isinstance(aliases, dict) else {}
        raw_states = dict(states) if isinstance(states, dict) else {}
        merged["states"] = {
            self._safe_text(key): self._sanitize_state(value)
            for key, value in raw_states.items()
            if self._safe_text(key) and isinstance(value, dict)
        }
        return merged

    def _write_unlocked(self, payload: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def compose_key(self, *, platform: str, platform_user_id: str) -> str:
        safe_platform = self._safe_text(platform).lower()
        safe_user_id = self._safe_text(platform_user_id)
        if not safe_platform or not safe_user_id:
            return self._safe_text(platform_user_id)
        return f"{safe_platform}::{safe_user_id}"

    def _resolve_key_unlocked(
        self,
        payload: Dict[str, Any],
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> str:
        explicit_runtime_key = self._safe_text(runtime_key)
        if explicit_runtime_key:
            return explicit_runtime_key
        safe_user_id = self._safe_text(platform_user_id)
        safe_platform = self._safe_text(platform).lower()
        if safe_platform and safe_user_id:
            return self.compose_key(platform=safe_platform, platform_user_id=safe_user_id)
        aliases = dict(payload.get("aliases") or {})
        if safe_user_id:
            aliased = self._safe_text(aliases.get(safe_user_id))
            if aliased:
                return aliased
        return safe_user_id

    def _normalize_active_task(
        self,
        task: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        if not isinstance(task, dict):
            return None
        now = _now_iso()
        normalized = {
            "id": self._safe_text(task.get("id"), 80),
            "session_task_id": self._safe_text(task.get("session_task_id"), 80),
            "task_inbox_id": self._safe_text(task.get("task_inbox_id"), 80),
            "goal": self._safe_text(task.get("goal"), 2000),
            "status": self._safe_text(task.get("status") or "running", 40).lower()
            or "running",
            "source": self._safe_text(task.get("source") or "message", 80) or "message",
            "created_at": self._safe_text(task.get("created_at") or now, 64) or now,
            "updated_at": self._safe_text(task.get("updated_at") or now, 64) or now,
            "result_summary": self._safe_text(task.get("result_summary"), 2000),
            "needs_confirmation": bool(task.get("needs_confirmation", False)),
            "confirmation_deadline": self._safe_text(task.get("confirmation_deadline"), 64),
            "stage_index": max(0, int(task.get("stage_index") or 0)),
            "stage_total": max(0, int(task.get("stage_total") or 0)),
            "stage_id": self._safe_text(task.get("stage_id"), 80),
            "stage_title": self._safe_text(task.get("stage_title"), 200),
            "attempt_index": max(0, int(task.get("attempt_index") or 0)),
            "last_blocking_reason": self._safe_text(task.get("last_blocking_reason"), 2000),
            "resume_instruction_preview": self._safe_text(
                task.get("resume_instruction_preview"), 2000
            ),
            "adjustments_count": max(0, int(task.get("adjustments_count") or 0)),
            "delivery_state": self._safe_text(task.get("delivery_state"), 40).lower(),
            "last_user_visible_summary": self._safe_text(
                task.get("last_user_visible_summary"), 2400
            ),
            "resume_window_until": self._safe_text(task.get("resume_window_until"), 64),
        }
        if not normalized["id"]:
            return None
        if not normalized["session_task_id"]:
            normalized["session_task_id"] = normalized["id"]
        return normalized

    def _state_for_update_unlocked(
        self,
        payload: Dict[str, Any],
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> tuple[str, Dict[str, Any]]:
        key = self._resolve_key_unlocked(
            payload,
            platform=platform,
            platform_user_id=platform_user_id,
            runtime_key=runtime_key,
        )
        states = payload.setdefault("states", {})
        if not isinstance(states, dict):
            states = {}
            payload["states"] = states
        state = states.get(key)
        state = self._sanitize_state(state if isinstance(state, dict) else {})
        states[key] = state
        safe_platform = self._safe_text(platform).lower()
        safe_user_id = self._safe_text(platform_user_id)
        if safe_platform:
            state["platform"] = safe_platform
        elif not state.get("platform") and "::" in key:
            state["platform"] = key.split("::", 1)[0]
        if safe_user_id:
            state["platform_user_id"] = safe_user_id
        elif not state.get("platform_user_id") and "::" in key:
            state["platform_user_id"] = key.split("::", 1)[1]
        if safe_user_id:
            aliases = payload.setdefault("aliases", {})
            if isinstance(aliases, dict):
                aliases[safe_user_id] = key
        state["active_task"] = self._normalize_active_task(state.get("active_task"))
        state["updated_at"] = _now_iso()
        return key, state

    def _sanitize_state(self, state: Dict[str, Any] | None) -> Dict[str, Any]:
        raw = dict(state or {})
        sanitized = {
            "platform": self._safe_text(raw.get("platform"), 64).lower(),
            "platform_user_id": self._safe_text(raw.get("platform_user_id"), 128),
            "session_id": self._safe_text(raw.get("session_id"), 120),
            "active_task": self._normalize_active_task(raw.get("active_task")),
            "updated_at": self._safe_text(raw.get("updated_at"), 64) or _now_iso(),
        }
        return sanitized

    def resolve_runtime_key(
        self,
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> str:
        with self._lock:
            payload = self._read_unlocked()
            return self._resolve_key_unlocked(
                payload,
                platform=platform,
                platform_user_id=platform_user_id,
                runtime_key=runtime_key,
            )

    def get_state(
        self,
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            payload = self._read_unlocked()
            key = self._resolve_key_unlocked(
                payload,
                platform=platform,
                platform_user_id=platform_user_id,
                runtime_key=runtime_key,
            )
            return self._sanitize_state((payload.get("states") or {}).get(key) or {})

    def get_session_id(
        self,
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> str:
        state = self.get_state(
            platform=platform,
            platform_user_id=platform_user_id,
            runtime_key=runtime_key,
        )
        return self._safe_text(state.get("session_id"), 120)

    def set_session_id(
        self,
        *,
        session_id: str,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> str:
        with self._lock:
            payload = self._read_unlocked()
            key, state = self._state_for_update_unlocked(
                payload,
                platform=platform,
                platform_user_id=platform_user_id,
                runtime_key=runtime_key,
            )
            state["session_id"] = self._safe_text(session_id, 120)
            self._write_unlocked(payload)
            return key

    def get_active_task(
        self,
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> Dict[str, Any] | None:
        state = self.get_state(
            platform=platform,
            platform_user_id=platform_user_id,
            runtime_key=runtime_key,
        )
        return self._normalize_active_task(state.get("active_task"))

    def set_active_task(
        self,
        task: Dict[str, Any],
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> Dict[str, Any] | None:
        normalized = self._normalize_active_task(task)
        with self._lock:
            payload = self._read_unlocked()
            _key, state = self._state_for_update_unlocked(
                payload,
                platform=platform,
                platform_user_id=platform_user_id,
                runtime_key=runtime_key,
            )
            state["active_task"] = normalized
            self._write_unlocked(payload)
        return normalized

    def update_active_task(
        self,
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
        **fields: Any,
    ) -> Dict[str, Any] | None:
        with self._lock:
            payload = self._read_unlocked()
            _key, state = self._state_for_update_unlocked(
                payload,
                platform=platform,
                platform_user_id=platform_user_id,
                runtime_key=runtime_key,
            )
            current = self._normalize_active_task(state.get("active_task"))
            if current is None:
                return None
            for key in (
                "session_task_id",
                "task_inbox_id",
                "goal",
                "status",
                "source",
                "result_summary",
                "needs_confirmation",
                "confirmation_deadline",
                "stage_index",
                "stage_total",
                "stage_id",
                "stage_title",
                "attempt_index",
                "last_blocking_reason",
                "resume_instruction_preview",
                "adjustments_count",
                "delivery_state",
                "last_user_visible_summary",
                "resume_window_until",
            ):
                if key in fields:
                    current[key] = fields[key]
            current["updated_at"] = _now_iso()
            terminal_statuses = {"done", "failed", "cancelled", "timed_out"}
            should_clear = bool(fields.get("clear_active")) or (
                self._safe_text(current.get("status")).lower() in terminal_statuses
            )
            state["active_task"] = None if should_clear else self._normalize_active_task(current)
            self._write_unlocked(payload)
            return self._normalize_active_task(state.get("active_task"))

    def clear_active_task(
        self,
        *,
        platform: str = "",
        platform_user_id: str = "",
        runtime_key: str = "",
    ) -> None:
        with self._lock:
            payload = self._read_unlocked()
            _key, state = self._state_for_update_unlocked(
                payload,
                platform=platform,
                platform_user_id=platform_user_id,
                runtime_key=runtime_key,
            )
            state["active_task"] = None
            self._write_unlocked(payload)


channel_runtime_store = ChannelRuntimeStore()
