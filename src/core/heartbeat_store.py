from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml

from core.config import DATA_DIR
from core.state_paths import SINGLE_USER_SCOPE

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now_local().isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_now_local().tzinfo)
    return dt


def _parse_hhmm(value: str, fallback: str) -> time:
    text = str(value or "").strip() or fallback
    try:
        hour_text, minute_text = (text.split(":", 1) + ["0"])[:2]
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour=hour, minute=minute)
    except Exception:
        pass
    fb_hour, fb_minute = (fallback.split(":", 1) + ["0"])[:2]
    return time(hour=int(fb_hour), minute=int(fb_minute))


def _parse_every_seconds(value: str) -> int:
    raw = str(value or "").strip().lower()
    if not raw:
        return 30 * 60
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", raw)
    if not match:
        return 30 * 60
    amount = max(1, int(match.group(1)))
    unit = match.group(2) or "m"
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 3600
    if unit == "d":
        return amount * 86400
    return 30 * 60


def _normalize_every(value: str) -> str:
    seconds = _parse_every_seconds(value)
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _truncate(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len]


class HeartbeatStore:
    """Single-user heartbeat configuration + runtime status store."""

    def __init__(self):
        self.root = (Path(DATA_DIR) / "runtime_tasks").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scope = SINGLE_USER_SCOPE
        self.lock_timeout_sec = max(
            1, int(os.getenv("HEARTBEAT_LOCK_TIMEOUT_SEC", "20"))
        )
        self.default_every = _normalize_every(os.getenv("HEARTBEAT_EVERY", "30m"))
        self.default_target = os.getenv("HEARTBEAT_TARGET", "last").strip() or "last"
        self.default_active_start = (
            os.getenv("HEARTBEAT_ACTIVE_START", "08:00").strip() or "08:00"
        )
        self.default_active_end = (
            os.getenv("HEARTBEAT_ACTIVE_END", "23:59").strip() or "23:59"
        )
        self.default_timezone = os.getenv("HEARTBEAT_TIMEZONE", "").strip()
        self.suppress_ok = os.getenv("HEARTBEAT_SUPPRESS_OK", "true").lower() == "true"
        self.session_event_keep = max(
            10, int(os.getenv("HEARTBEAT_SESSION_EVENT_KEEP", "40"))
        )
        self._locks: dict[str, asyncio.Lock] = {}

    def _docs_root(self) -> Path:
        path = self.root.parent.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def heartbeat_path(self, user_id: str) -> Path:
        _ = user_id
        return (self._docs_root() / "HEARTBEAT.md").resolve()

    def status_path(self, user_id: str) -> Path:
        _ = user_id
        self.root.mkdir(parents=True, exist_ok=True)
        return (self.root / "STATUS.json").resolve()

    def backup_legacy_path(self, user_id: str) -> Path:
        _ = user_id
        return (self._docs_root() / "HEARTBEAT.v1.bak.md").resolve()

    def _scope_lock(self) -> asyncio.Lock:
        lock = self._locks.get(self.scope)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[self.scope] = lock
        return lock

    def _default_spec(self) -> dict[str, Any]:
        return {
            "version": 2,
            "every": self.default_every,
            "target": self.default_target,
            "active_hours": {
                "start": self.default_active_start,
                "end": self.default_active_end,
            },
            "paused": False,
            "updated_at": _now_iso(),
        }

    def _default_status(self) -> dict[str, Any]:
        return {
            "version": 2,
            "locked_by": "",
            "lock_expires_at": "",
            "last_update": _now_iso(),
            "last_error": "",
            "heartbeat": {
                "last_run_at": "",
                "last_result": "",
                "next_due_at": "",
                "last_level": "OK",
            },
            "delivery": {
                "last_platform": "",
                "last_chat_id": "",
                "last_session_id": "",
            },
            "session": {
                "active_task": None,
                "active_executor_id": "",
                "last_event": "",
                "events": [],
            },
            "migration_notes": [],
        }

    def _normalize_active_task(
        self, task: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if not isinstance(task, dict):
            return None
        now = _now_iso()
        normalized = {
            "id": _truncate(task.get("id", ""), 80),
            "session_task_id": _truncate(task.get("session_task_id", ""), 80),
            "task_inbox_id": _truncate(task.get("task_inbox_id", ""), 80),
            "goal": _truncate(task.get("goal", ""), 2000),
            "status": _truncate(task.get("status", "running"), 40).lower() or "running",
            "source": _truncate(task.get("source", "message"), 80) or "message",
            "created_at": _truncate(task.get("created_at", now), 64) or now,
            "updated_at": _truncate(task.get("updated_at", now), 64) or now,
            "result_summary": _truncate(task.get("result_summary", ""), 2000),
            "needs_confirmation": bool(task.get("needs_confirmation", False)),
            "confirmation_deadline": _truncate(
                task.get("confirmation_deadline", ""), 64
            ),
            "stage_index": max(0, int(task.get("stage_index") or 0)),
            "stage_total": max(0, int(task.get("stage_total") or 0)),
            "stage_id": _truncate(task.get("stage_id", ""), 80),
            "stage_title": _truncate(task.get("stage_title", ""), 200),
            "attempt_index": max(0, int(task.get("attempt_index") or 0)),
            "last_blocking_reason": _truncate(
                task.get("last_blocking_reason", ""), 2000
            ),
            "resume_instruction_preview": _truncate(
                task.get("resume_instruction_preview", ""), 2000
            ),
            "adjustments_count": max(0, int(task.get("adjustments_count") or 0)),
            "delivery_state": _truncate(task.get("delivery_state", ""), 40).lower(),
            "last_user_visible_summary": _truncate(
                task.get("last_user_visible_summary", ""), 2400
            ),
            "resume_window_until": _truncate(task.get("resume_window_until", ""), 64),
        }
        if not normalized["id"]:
            return None
        if not normalized["session_task_id"]:
            normalized["session_task_id"] = normalized["id"]
        return normalized

    def _normalize_spec(self, data: dict[str, Any] | None) -> dict[str, Any]:
        default = self._default_spec()
        merged = dict(default)
        merged.update(dict(data or {}))
        active = dict(default["active_hours"])
        active.update(dict((data or {}).get("active_hours") or {}))
        active["start"] = (
            _truncate(active.get("start", self.default_active_start), 5)
            or self.default_active_start
        )
        active["end"] = (
            _truncate(active.get("end", self.default_active_end), 5)
            or self.default_active_end
        )
        return {
            "version": 2,
            "every": _normalize_every(str(merged.get("every", self.default_every))),
            "target": _truncate(merged.get("target", self.default_target), 40)
            or self.default_target,
            "active_hours": active,
            "paused": bool(merged.get("paused", False)),
            "updated_at": _truncate(merged.get("updated_at", _now_iso()), 64)
            or _now_iso(),
        }

    def _normalize_status(self, data: dict[str, Any] | None) -> dict[str, Any]:
        default = self._default_status()
        merged = dict(default)
        merged.update(dict(data or {}))

        heartbeat = dict(default["heartbeat"])
        heartbeat.update(dict((data or {}).get("heartbeat") or {}))
        heartbeat["last_run_at"] = _truncate(heartbeat.get("last_run_at", ""), 64)
        heartbeat["last_result"] = _truncate(heartbeat.get("last_result", ""), 4000)
        heartbeat["next_due_at"] = _truncate(heartbeat.get("next_due_at", ""), 64)
        level = _truncate(heartbeat.get("last_level", "OK"), 16).upper() or "OK"
        if level not in {"OK", "NOTICE", "ACTION"}:
            level = "NOTICE"
        heartbeat["last_level"] = level

        delivery = dict(default["delivery"])
        delivery.update(dict((data or {}).get("delivery") or {}))
        delivery["last_platform"] = _truncate(delivery.get("last_platform", ""), 64)
        delivery["last_chat_id"] = _truncate(delivery.get("last_chat_id", ""), 128)
        delivery["last_session_id"] = _truncate(
            delivery.get("last_session_id", ""),
            120,
        )

        session = dict(default["session"])
        session.update(dict((data or {}).get("session") or {}))
        session["active_task"] = self._normalize_active_task(session.get("active_task"))
        session["active_executor_id"] = _truncate(
            session.get("active_executor_id", ""),
            80,
        )
        session["last_event"] = _truncate(session.get("last_event", ""), 800)
        raw_events = session.get("events")
        if not isinstance(raw_events, list):
            raw_events = []
        session["events"] = [
            _truncate(item, 800) for item in raw_events if str(item or "").strip()
        ][-self.session_event_keep :]

        notes = merged.get("migration_notes")
        if not isinstance(notes, list):
            notes = []
        notes = [_truncate(item, 500) for item in notes if str(item or "").strip()][-20:]

        return {
            "version": 2,
            "locked_by": _truncate(merged.get("locked_by", ""), 200),
            "lock_expires_at": _truncate(merged.get("lock_expires_at", ""), 64),
            "last_update": _truncate(merged.get("last_update", _now_iso()), 64)
            or _now_iso(),
            "last_error": _truncate(merged.get("last_error", ""), 1000),
            "heartbeat": heartbeat,
            "delivery": delivery,
            "session": session,
            "migration_notes": notes,
        }

    def _parse_markdown(self, text: str) -> tuple[dict[str, Any], list[str]]:
        data: dict[str, Any] = {}
        checklist: list[str] = []
        raw = str(text or "")
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                try:
                    loaded = yaml.safe_load(parts[1]) or {}
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}
                body = parts[2]
        in_checklist = False
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.lower() == "# heartbeat checklist":
                in_checklist = True
                continue
            if in_checklist and stripped.startswith("- "):
                item = stripped[2:].strip()
                if item:
                    checklist.append(item)
            elif in_checklist and stripped.startswith("#"):
                break
        return data, checklist

    def _render_markdown(self, spec: dict[str, Any], checklist: list[str]) -> str:
        payload = self._normalize_spec(spec)
        header = yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        lines = ["# Heartbeat checklist", ""]
        if checklist:
            for item in checklist:
                normalized = _truncate(item, 400)
                if normalized:
                    lines.append(f"- {normalized}")
        else:
            lines.append("- 检查自己和后台任务的运行状态是否良好")
        body = "\n".join(lines).rstrip() + "\n"
        return f"---\n{header}\n---\n\n{body}"

    def _is_legacy_heartbeat(self, data: dict[str, Any], raw_text: str) -> bool:
        if "tasks" in data:
            return True
        if int(data.get("version", 1) or 1) == 2:
            return False
        lowered = raw_text.lower()
        return "## events" in lowered or "# heartbeat" in lowered

    def _summarize_legacy(self, data: dict[str, Any], raw_text: str) -> str:
        tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        summary_parts: list[str] = []
        if tasks:
            summary_parts.append(f"legacy tasks={len(tasks)}")
            latest = tasks[-1]
            if isinstance(latest, dict):
                latest_id = _truncate(latest.get("id", ""), 30)
                latest_status = _truncate(latest.get("status", ""), 20)
                summary_parts.append(f"latest={latest_id}:{latest_status}")
        event_lines = [
            line.strip()[2:].strip()
            for line in raw_text.splitlines()
            if line.strip().startswith("- ")
        ]
        if event_lines:
            summary_parts.append(f"legacy_events={len(event_lines)}")
            summary_parts.append(f"tail={_truncate(event_lines[-1], 180)}")
        if not summary_parts:
            summary_parts.append("legacy heartbeat payload migrated")
        return "; ".join(summary_parts)

    @staticmethod
    def _read_json_payload(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(loaded, dict):
            return None
        return loaded

    def _heartbeat_candidates(self) -> list[Path]:
        docs_root = self._docs_root()
        paths: list[Path] = []
        canonical = self.heartbeat_path(self.scope)
        if canonical.exists():
            paths.append(canonical)
        for path in sorted(docs_root.glob("HEARTBEAT*.md")):
            resolved = path.resolve()
            if not resolved.is_file():
                continue
            if resolved == canonical:
                continue
            if resolved.name.endswith(".v1.bak.md"):
                continue
            paths.append(resolved)
        for path in sorted(self.root.rglob("HEARTBEAT.md")):
            resolved = path.resolve()
            if resolved not in paths:
                paths.append(resolved)
        return paths

    def _status_candidates(self) -> list[Path]:
        canonical = self.status_path(self.scope)
        paths: list[Path] = []
        if canonical.exists():
            paths.append(canonical)
        for path in sorted(self.root.rglob("STATUS.json")):
            resolved = path.resolve()
            if resolved == canonical:
                continue
            paths.append(resolved)
        return paths

    def _has_existing_state_unlocked(self) -> bool:
        return bool(self._heartbeat_candidates() or self._status_candidates())

    def _choose_best_heartbeat(self) -> tuple[dict[str, Any], list[str], list[str]]:
        default_spec = self._default_spec()
        best_spec = default_spec
        best_checklist: list[str] = []
        notes: list[str] = []
        best_score = -1.0
        for path in self._heartbeat_candidates():
            try:
                raw_text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            parsed, checklist = self._parse_markdown(raw_text)
            entry_notes: list[str] = []
            if self._is_legacy_heartbeat(parsed, raw_text):
                backup = self.backup_legacy_path(self.scope)
                if not backup.exists():
                    backup.write_text(raw_text, encoding="utf-8")
                entry_notes.append(f"{_now_iso()} | {self._summarize_legacy(parsed, raw_text)}")
                parsed = default_spec
                checklist = []
            spec = self._normalize_spec(parsed)
            try:
                mtime_score = path.stat().st_mtime
            except Exception:
                mtime_score = 0.0
            score = (len(checklist) * 10_000_000.0) + mtime_score
            if score <= best_score:
                continue
            best_score = score
            best_spec = spec
            best_checklist = checklist
            notes = entry_notes
        return best_spec, best_checklist, notes

    def _choose_best_status(self) -> tuple[dict[str, Any], list[str]]:
        best_status = self._default_status()
        notes: list[str] = []
        best_score = -1.0
        for path in self._status_candidates():
            payload = self._read_json_payload(path)
            if payload is None:
                continue
            status = self._normalize_status(payload)
            try:
                mtime_score = path.stat().st_mtime
            except Exception:
                mtime_score = 0.0
            score = mtime_score
            if score <= best_score:
                continue
            best_score = score
            best_status = status
            notes = []
        return best_status, notes

    def _write_status_unlocked(self, status: dict[str, Any]) -> dict[str, Any]:
        path = self.status_path(self.scope)
        normalized = self._normalize_status(status)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return normalized

    def _cleanup_legacy_layout_unlocked(self) -> int:
        canonical_hb = self.heartbeat_path(self.scope)
        canonical_status = self.status_path(self.scope)
        removed = 0

        for path in self._heartbeat_candidates():
            if path == canonical_hb:
                continue
            with contextlib.suppress(Exception):
                path.unlink()
                removed += 1

        for path in self._status_candidates():
            if path == canonical_status:
                continue
            with contextlib.suppress(Exception):
                path.unlink()
                removed += 1

        for marker in sorted(self.root.rglob(".legacy-import-complete")):
            with contextlib.suppress(Exception):
                marker.unlink()
                removed += 1

        for child in sorted(self.root.iterdir(), reverse=True):
            if child == canonical_status:
                continue
            if child.is_file():
                if child.name == "STATUS.json":
                    continue
                with contextlib.suppress(Exception):
                    child.unlink()
                    removed += 1
                continue
            if child.is_dir():
                with contextlib.suppress(Exception):
                    shutil.rmtree(child)
                    removed += 1
        return removed

    def _ensure_canonical_unlocked(
        self,
        *,
        materialize: bool = True,
    ) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
        if not materialize and not self._has_existing_state_unlocked():
            return self._default_spec(), [], self._default_status()
        self.root.mkdir(parents=True, exist_ok=True)
        spec, checklist, hb_notes = self._choose_best_heartbeat()
        status, status_notes = self._choose_best_status()
        notes = list(status.get("migration_notes") or [])
        notes.extend(hb_notes)
        notes.extend(status_notes)
        if notes:
            status["migration_notes"] = notes[-20:]
            status["last_update"] = _now_iso()
        canonical_hb = self.heartbeat_path(self.scope)
        canonical_hb.write_text(
            self._render_markdown(spec, checklist),
            encoding="utf-8",
        )
        normalized_status = self._write_status_unlocked(status)
        return self._normalize_spec(spec), list(checklist), normalized_status

    async def ensure_user_files(self, user_id: str) -> None:
        _ = user_id
        async with self._scope_lock():
            self._ensure_canonical_unlocked()

    async def get_state(self, user_id: str) -> dict[str, Any]:
        _ = user_id
        async with self._scope_lock():
            spec, checklist, status = self._ensure_canonical_unlocked()
            return {
                "spec": spec,
                "checklist": checklist,
                "status": status,
            }

    async def list_users(self) -> list[str]:
        async with self._scope_lock():
            return [self.scope] if self._has_existing_state_unlocked() else []

    async def compact_user(self, user_id: str) -> None:
        _ = user_id
        async with self._scope_lock():
            spec, checklist, status = self._ensure_canonical_unlocked()
            spec["updated_at"] = _now_iso()
            self.heartbeat_path(self.scope).write_text(
                self._render_markdown(spec, checklist),
                encoding="utf-8",
            )
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)

    async def compact_all_users(self) -> int:
        await self.normalize_runtime_tree()
        users = await self.list_users()
        if not users:
            return 0
        await self.compact_user(self.scope)
        return len(users)

    async def get_heartbeat_spec(self, user_id: str) -> dict[str, Any]:
        state = await self.get_state(user_id)
        spec = dict(state["spec"])
        spec["checklist"] = list(state["checklist"])
        return spec

    async def set_heartbeat_spec(
        self,
        user_id: str,
        *,
        every: str | None = None,
        target: str | None = None,
        active_start: str | None = None,
        active_end: str | None = None,
        paused: bool | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        async with self._scope_lock():
            spec, checklist, status = self._ensure_canonical_unlocked()
            if every is not None:
                spec["every"] = _normalize_every(every)
            if target is not None:
                spec["target"] = _truncate(target, 40) or self.default_target
            if active_start is not None:
                spec.setdefault("active_hours", {})["start"] = _truncate(
                    active_start, 5
                )
            if active_end is not None:
                spec.setdefault("active_hours", {})["end"] = _truncate(active_end, 5)
            if paused is not None:
                spec["paused"] = bool(paused)
            spec["updated_at"] = _now_iso()
            self.heartbeat_path(self.scope).write_text(
                self._render_markdown(spec, checklist),
                encoding="utf-8",
            )
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            normalized = self._normalize_spec(spec)
            normalized["checklist"] = list(checklist)
            return normalized

    async def list_checklist(self, user_id: str) -> list[str]:
        state = await self.get_state(user_id)
        return list(state["checklist"])

    async def add_checklist_item(self, user_id: str, item: str) -> list[str]:
        _ = user_id
        normalized = _truncate(item, 400)
        if not normalized:
            return await self.list_checklist(self.scope)
        async with self._scope_lock():
            spec, checklist, status = self._ensure_canonical_unlocked()
            if normalized not in checklist:
                checklist.append(normalized)
                spec["updated_at"] = _now_iso()
                self.heartbeat_path(self.scope).write_text(
                    self._render_markdown(spec, checklist),
                    encoding="utf-8",
                )
                status["last_update"] = _now_iso()
                self._write_status_unlocked(status)
            return list(checklist)

    async def remove_checklist_item(self, user_id: str, index: int) -> list[str]:
        _ = user_id
        async with self._scope_lock():
            spec, checklist, status = self._ensure_canonical_unlocked()
            if 1 <= index <= len(checklist):
                checklist.pop(index - 1)
                spec["updated_at"] = _now_iso()
                self.heartbeat_path(self.scope).write_text(
                    self._render_markdown(spec, checklist),
                    encoding="utf-8",
                )
                status["last_update"] = _now_iso()
                self._write_status_unlocked(status)
            return list(checklist)

    async def mark_heartbeat_run(
        self,
        user_id: str,
        result: str,
        *,
        run_at: str | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        stamp = run_at or _now_iso()
        async with self._scope_lock():
            spec, _checklist, status = self._ensure_canonical_unlocked()
            every_sec = _parse_every_seconds(spec.get("every", self.default_every))
            run_dt = _parse_iso(stamp) or _now_local()
            next_due = (run_dt + timedelta(seconds=every_sec)).isoformat(
                timespec="seconds"
            )
            heartbeat = dict(status.get("heartbeat") or {})
            heartbeat["last_run_at"] = stamp
            heartbeat["last_result"] = _truncate(result, 4000)
            heartbeat["next_due_at"] = next_due
            heartbeat["last_level"] = self.classify_result(result)
            status["heartbeat"] = heartbeat
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            return dict(heartbeat)

    @staticmethod
    def classify_result(result: str) -> str:
        text = str(result or "").strip()
        if not text or text.upper() == "HEARTBEAT_OK":
            return "OK"
        lowered = text.lower()
        action_tokens = (
            "需要",
            "请",
            "修复",
            "异常",
            "失败",
            "error",
            "action",
            "todo",
            "risk",
            "告警",
            "建议立即",
        )
        notice_tokens = ("提醒", "notice", "建议", "info", "提示", "建议关注")
        if any(token in lowered for token in action_tokens):
            return "ACTION"
        if any(token in lowered for token in notice_tokens):
            return "NOTICE"
        return "NOTICE"

    async def normalize_runtime_tree(self) -> int:
        async with self._scope_lock():
            if not self._has_existing_state_unlocked():
                return 0
            self._ensure_canonical_unlocked()
            return self._cleanup_legacy_layout_unlocked()

    async def get_delivery_target(self, user_id: str) -> dict[str, str]:
        state = await self.get_state(user_id)
        delivery = state["status"].get("delivery") or {}
        return {
            "platform": str(delivery.get("last_platform", "")).strip(),
            "chat_id": str(delivery.get("last_chat_id", "")).strip(),
            "session_id": str(delivery.get("last_session_id", "")).strip(),
        }

    async def set_delivery_target(
        self,
        user_id: str,
        platform: str,
        chat_id: str,
        session_id: str = "",
    ) -> None:
        _ = user_id
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            delivery = dict(status.get("delivery") or {})
            delivery["last_platform"] = _truncate(platform, 64)
            delivery["last_chat_id"] = _truncate(chat_id, 128)
            bound_session_id = _truncate(session_id, 120)
            if bound_session_id:
                delivery["last_session_id"] = bound_session_id
            else:
                delivery["last_session_id"] = _truncate(
                    delivery.get("last_session_id", ""),
                    120,
                )
            status["delivery"] = delivery
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)

    async def get_session_active_task(self, user_id: str) -> dict[str, Any] | None:
        state = await self.get_state(user_id)
        task = (state["status"].get("session") or {}).get("active_task")
        return self._normalize_active_task(task)

    async def set_session_active_task(
        self, user_id: str, task: dict[str, Any]
    ) -> dict[str, Any] | None:
        _ = user_id
        normalized = self._normalize_active_task(task)
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            session = dict(status.get("session") or {})
            session["active_task"] = normalized
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            return normalized

    async def update_session_active_task(
        self, user_id: str, **fields
    ) -> dict[str, Any] | None:
        _ = user_id
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            session = dict(status.get("session") or {})
            current = self._normalize_active_task(session.get("active_task"))
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
                str(current.get("status", "")).strip().lower() in terminal_statuses
            )
            session["active_task"] = None if should_clear else self._normalize_active_task(current)
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            return self._normalize_active_task(session.get("active_task"))

    async def clear_session_active_task(self, user_id: str) -> None:
        _ = user_id
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            session = dict(status.get("session") or {})
            session["active_task"] = None
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)

    async def append_session_event(self, user_id: str, message: str) -> None:
        _ = user_id
        note = _truncate(message.replace("\r", " ").replace("\n", " ").strip(), 800)
        if not note:
            return
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            session = dict(status.get("session") or {})
            events = session.get("events")
            if not isinstance(events, list):
                events = []
            stamped = f"{_now_iso()} | {note}"
            events.append(stamped)
            session["events"] = events[-self.session_event_keep :]
            session["last_event"] = stamped
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)

    async def get_active_executor_id(self, user_id: str) -> str:
        state = await self.get_state(user_id)
        session = state["status"].get("session") or {}
        return str(session.get("active_executor_id", "")).strip()

    async def set_active_executor_id(self, user_id: str, executor_id: str) -> str:
        _ = user_id
        safe = _truncate(executor_id, 80)
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            session = dict(status.get("session") or {})
            session["active_executor_id"] = safe
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            return safe

    async def pulse(self, user_id: str, note: str = "") -> None:
        _ = user_id
        if note:
            await self.append_session_event(self.scope, f"pulse: {note}")
            return
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)

    async def claim_lock(
        self, user_id: str, owner: str, ttl_sec: int | None = None
    ) -> bool:
        _ = user_id
        lock_ttl = max(1, int(ttl_sec or self.lock_timeout_sec))
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            current_owner = str(status.get("locked_by", "")).strip()
            expires = _parse_iso(str(status.get("lock_expires_at", "")).strip())
            now = _now_local()
            expired = expires is None or expires <= now
            if current_owner and current_owner != owner and not expired:
                return False
            status["locked_by"] = str(owner)
            status["lock_expires_at"] = (now + timedelta(seconds=lock_ttl)).isoformat(
                timespec="seconds"
            )
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            return True

    async def refresh_lock(
        self, user_id: str, owner: str, ttl_sec: int | None = None
    ) -> bool:
        _ = user_id
        lock_ttl = max(1, int(ttl_sec or self.lock_timeout_sec))
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            if str(status.get("locked_by", "")).strip() != str(owner):
                return False
            status["lock_expires_at"] = (
                _now_local() + timedelta(seconds=lock_ttl)
            ).isoformat(timespec="seconds")
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            return True

    async def release_lock(self, user_id: str, owner: str | None = None) -> bool:
        _ = user_id
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            current_owner = str(status.get("locked_by", "")).strip()
            if owner and current_owner and current_owner != str(owner):
                return False
            status["locked_by"] = ""
            status["lock_expires_at"] = ""
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)
            return True

    async def set_last_error(self, user_id: str, error: str) -> None:
        _ = user_id
        async with self._scope_lock():
            _spec, _checklist, status = self._ensure_canonical_unlocked()
            status["last_error"] = _truncate(error, 1000)
            status["last_update"] = _now_iso()
            self._write_status_unlocked(status)

    async def clear_last_error(self, user_id: str) -> None:
        await self.set_last_error(user_id, "")

    def _resolve_now_for_spec(self, spec: dict[str, Any]) -> datetime:
        tz_name = self.default_timezone
        if ZoneInfo and tz_name:
            try:
                return datetime.now(ZoneInfo(tz_name))
            except Exception:
                return _now_local()
        return _now_local()

    def _is_in_active_hours(self, spec: dict[str, Any], now_dt: datetime) -> bool:
        active = spec.get("active_hours") or {}
        start_t = _parse_hhmm(
            str(active.get("start", self.default_active_start)),
            self.default_active_start,
        )
        end_t = _parse_hhmm(
            str(active.get("end", self.default_active_end)),
            self.default_active_end,
        )
        now_t = now_dt.time()
        if start_t <= end_t:
            return start_t <= now_t <= end_t
        return now_t >= start_t or now_t <= end_t

    async def should_run_heartbeat(self, user_id: str, force: bool = False) -> bool:
        state = await self.get_state(user_id)
        spec = state["spec"]
        status = state["status"]
        if force:
            return True
        if bool(spec.get("paused", False)):
            return False
        now_dt = self._resolve_now_for_spec(spec)
        if not self._is_in_active_hours(spec, now_dt):
            return False
        every_sec = _parse_every_seconds(spec.get("every", self.default_every))
        last_run_at = str((status.get("heartbeat") or {}).get("last_run_at", "")).strip()
        last_run = _parse_iso(last_run_at)
        if last_run is None:
            return True
        return (now_dt - last_run).total_seconds() >= every_sec


heartbeat_store = HeartbeatStore()
