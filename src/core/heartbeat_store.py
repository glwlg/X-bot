import asyncio
import json
import os
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from core.config import DATA_DIR

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python runtime fallback
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
        if dt.tzinfo is None:
            return dt.replace(tzinfo=_now_local().tzinfo)
        return dt
    except Exception:
        return None


def _parse_hhmm(value: str, fallback: str) -> time:
    text = str(value or "").strip() or fallback
    try:
        parts = text.split(":", 1)
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour=hour, minute=minute)
    except Exception:
        pass
    fb_parts = fallback.split(":", 1)
    return time(hour=int(fb_parts[0]), minute=int(fb_parts[1]))


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
    """Per-user heartbeat configuration + runtime status store."""

    def __init__(self):
        self.root = (Path(DATA_DIR) / "runtime_tasks").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_timeout_sec = max(1, int(os.getenv("HEARTBEAT_LOCK_TIMEOUT_SEC", "20")))
        self.default_every = _normalize_every(os.getenv("HEARTBEAT_EVERY", "30m"))
        self.default_target = os.getenv("HEARTBEAT_TARGET", "last").strip() or "last"
        self.default_active_start = os.getenv("HEARTBEAT_ACTIVE_START", "08:00").strip() or "08:00"
        self.default_active_end = os.getenv("HEARTBEAT_ACTIVE_END", "22:00").strip() or "22:00"
        self.default_timezone = os.getenv("HEARTBEAT_TIMEZONE", "").strip()
        self.suppress_ok = os.getenv("HEARTBEAT_SUPPRESS_OK", "true").lower() == "true"
        self.session_event_keep = max(10, int(os.getenv("HEARTBEAT_SESSION_EVENT_KEEP", "40")))
        self._locks: Dict[str, asyncio.Lock] = {}

    def heartbeat_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "HEARTBEAT.md"

    def status_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "STATUS.json"

    def backup_legacy_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "HEARTBEAT.v1.bak.md"

    def _user_dir(self, user_id: str) -> Path:
        safe = str(user_id).strip() or "unknown"
        path = (self.root / safe).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _user_lock(self, user_id: str) -> asyncio.Lock:
        key = str(user_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _default_spec(self, user_id: str) -> Dict[str, Any]:
        return {
            "version": 2,
            "user_id": str(user_id),
            "every": self.default_every,
            "target": self.default_target,
            "active_hours": {
                "start": self.default_active_start,
                "end": self.default_active_end,
            },
            "paused": False,
            "updated_at": _now_iso(),
        }

    def _default_status(self, user_id: str) -> Dict[str, Any]:
        return {
            "version": 2,
            "user_id": str(user_id),
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
            },
            "session": {
                "active_task": None,
                "active_worker_id": "worker-main",
                "last_event": "",
                "events": [],
            },
            "migration_notes": [],
        }

    def _normalize_active_task(self, task: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not isinstance(task, dict):
            return None
        now = _now_iso()
        normalized = {
            "id": _truncate(task.get("id", ""), 80),
            "goal": _truncate(task.get("goal", ""), 2000),
            "status": _truncate(task.get("status", "running"), 40).lower() or "running",
            "source": _truncate(task.get("source", "message"), 80) or "message",
            "created_at": _truncate(task.get("created_at", now), 64) or now,
            "updated_at": _truncate(task.get("updated_at", now), 64) or now,
            "result_summary": _truncate(task.get("result_summary", ""), 2000),
            "needs_confirmation": bool(task.get("needs_confirmation", False)),
            "confirmation_deadline": _truncate(task.get("confirmation_deadline", ""), 64),
        }
        if not normalized["id"]:
            return None
        return normalized

    def _normalize_status(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        default = self._default_status(user_id)
        merged = dict(default)
        merged.update(data or {})

        heartbeat = dict(default["heartbeat"])
        heartbeat.update((data or {}).get("heartbeat") or {})
        heartbeat["last_run_at"] = _truncate(heartbeat.get("last_run_at", ""), 64)
        heartbeat["last_result"] = _truncate(heartbeat.get("last_result", ""), 4000)
        heartbeat["next_due_at"] = _truncate(heartbeat.get("next_due_at", ""), 64)
        level = _truncate(heartbeat.get("last_level", "OK"), 16).upper() or "OK"
        if level not in {"OK", "NOTICE", "ACTION"}:
            level = "NOTICE"
        heartbeat["last_level"] = level
        merged["heartbeat"] = heartbeat

        delivery = dict(default["delivery"])
        delivery.update((data or {}).get("delivery") or {})
        delivery["last_platform"] = _truncate(delivery.get("last_platform", ""), 64)
        delivery["last_chat_id"] = _truncate(delivery.get("last_chat_id", ""), 128)
        merged["delivery"] = delivery

        session = dict(default["session"])
        session.update((data or {}).get("session") or {})
        session["active_task"] = self._normalize_active_task(session.get("active_task"))
        session["active_worker_id"] = _truncate(session.get("active_worker_id", "worker-main"), 80) or "worker-main"
        session["last_event"] = _truncate(session.get("last_event", ""), 800)
        raw_events = session.get("events")
        if not isinstance(raw_events, list):
            raw_events = []
        session["events"] = [_truncate(item, 800) for item in raw_events if str(item or "").strip()][
            -self.session_event_keep :
        ]
        merged["session"] = session

        notes = merged.get("migration_notes")
        if not isinstance(notes, list):
            notes = []
        merged["migration_notes"] = [_truncate(item, 500) for item in notes if str(item or "").strip()][
            -20:
        ]

        merged["version"] = 2
        merged["user_id"] = str(user_id)
        merged["locked_by"] = _truncate(merged.get("locked_by", ""), 200)
        merged["lock_expires_at"] = _truncate(merged.get("lock_expires_at", ""), 64)
        merged["last_update"] = _truncate(merged.get("last_update", _now_iso()), 64) or _now_iso()
        merged["last_error"] = _truncate(merged.get("last_error", ""), 1000)
        return merged

    def _normalize_spec(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        default = self._default_spec(user_id)
        merged = dict(default)
        merged.update(data or {})

        active = dict(default["active_hours"])
        active.update((data or {}).get("active_hours") or {})
        active["start"] = _truncate(active.get("start", self.default_active_start), 5) or self.default_active_start
        active["end"] = _truncate(active.get("end", self.default_active_end), 5) or self.default_active_end

        merged["version"] = 2
        merged["user_id"] = str(user_id)
        merged["every"] = _normalize_every(str(merged.get("every", self.default_every)))
        merged["target"] = _truncate(merged.get("target", self.default_target), 40) or self.default_target
        merged["active_hours"] = active
        merged["paused"] = bool(merged.get("paused", False))
        merged["updated_at"] = _truncate(merged.get("updated_at", _now_iso()), 64) or _now_iso()
        return merged

    def _parse_markdown(self, text: str) -> Tuple[Dict[str, Any], List[str]]:
        data: Dict[str, Any] = {}
        checklist: List[str] = []
        raw = text or ""

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

    def _render_markdown(self, spec: Dict[str, Any], checklist: List[str]) -> str:
        payload = self._normalize_spec(str(spec.get("user_id", "")), spec)
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
            lines.append("- Check critical updates and only notify when action is needed")
        body = "\n".join(lines).rstrip() + "\n"
        return f"---\n{header}\n---\n\n{body}"

    def _is_legacy_heartbeat(self, data: Dict[str, Any], raw_text: str) -> bool:
        if "tasks" in data:
            return True
        if int(data.get("version", 1) or 1) == 2:
            return False
        lowered = raw_text.lower()
        if "## events" in lowered or "# heartbeat" in lowered:
            return True
        return False

    def _summarize_legacy(self, data: Dict[str, Any], raw_text: str) -> str:
        tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        summary_parts: List[str] = []
        if tasks:
            summary_parts.append(f"legacy tasks={len(tasks)}")
            latest = tasks[-1]
            if isinstance(latest, dict):
                latest_id = _truncate(latest.get("id", ""), 30)
                latest_status = _truncate(latest.get("status", ""), 20)
                summary_parts.append(f"latest={latest_id}:{latest_status}")
        event_lines = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                event_lines.append(stripped[2:].strip())
        if event_lines:
            summary_parts.append(f"legacy_events={len(event_lines)}")
            summary_parts.append(f"tail={_truncate(event_lines[-1], 180)}")
        if not summary_parts:
            summary_parts.append("legacy heartbeat payload migrated")
        return "; ".join(summary_parts)

    def _read_status_raw_unlocked(self, user_id: str) -> Dict[str, Any]:
        path = self.status_path(user_id)
        if not path.exists():
            data = self._default_status(user_id)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return data
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                loaded = {}
        except Exception:
            loaded = {}
        normalized = self._normalize_status(user_id, loaded)
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        return normalized

    def _write_status_unlocked(self, user_id: str, status: Dict[str, Any]) -> None:
        path = self.status_path(user_id)
        normalized = self._normalize_status(user_id, status)
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ensure_user_files_unlocked(self, user_id: str) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
        hb_path = self.heartbeat_path(user_id)
        status = self._read_status_raw_unlocked(user_id)

        if not hb_path.exists():
            spec = self._default_spec(user_id)
            checklist: List[str] = []
            hb_path.write_text(self._render_markdown(spec, checklist), encoding="utf-8")
            return spec, checklist, status

        raw_text = hb_path.read_text(encoding="utf-8")
        parsed, checklist = self._parse_markdown(raw_text)

        migration_note = ""
        if self._is_legacy_heartbeat(parsed, raw_text):
            backup = self.backup_legacy_path(user_id)
            if not backup.exists():
                backup.write_text(raw_text, encoding="utf-8")
            migration_note = self._summarize_legacy(parsed, raw_text)
            parsed = self._default_spec(user_id)
            checklist = []

        spec = self._normalize_spec(user_id, parsed)
        hb_path.write_text(self._render_markdown(spec, checklist), encoding="utf-8")

        if migration_note:
            notes = status.get("migration_notes")
            if not isinstance(notes, list):
                notes = []
            notes.append(f"{_now_iso()} | {migration_note}")
            status["migration_notes"] = notes[-20:]
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
            status = self._read_status_raw_unlocked(user_id)

        return spec, checklist, status

    async def ensure_user_files(self, user_id: str) -> None:
        async with self._user_lock(user_id):
            self._ensure_user_files_unlocked(user_id)

    async def get_state(self, user_id: str) -> Dict[str, Any]:
        async with self._user_lock(user_id):
            spec, checklist, status = self._ensure_user_files_unlocked(user_id)
            return {
                "spec": spec,
                "checklist": checklist,
                "status": status,
            }

    async def list_users(self) -> List[str]:
        users: List[str] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            if (child / "HEARTBEAT.md").exists() or (child / "STATUS.json").exists():
                users.append(child.name)
        return users

    async def compact_user(self, user_id: str) -> None:
        async with self._user_lock(user_id):
            spec, checklist, status = self._ensure_user_files_unlocked(user_id)
            spec["updated_at"] = _now_iso()
            self.heartbeat_path(user_id).write_text(
                self._render_markdown(spec, checklist), encoding="utf-8"
            )
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)

    async def compact_all_users(self) -> int:
        await self.normalize_runtime_tree()
        users = await self.list_users()
        for user_id in users:
            await self.compact_user(user_id)
        return len(users)

    async def get_heartbeat_spec(self, user_id: str) -> Dict[str, Any]:
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
    ) -> Dict[str, Any]:
        async with self._user_lock(user_id):
            spec, checklist, status = self._ensure_user_files_unlocked(user_id)
            if every is not None:
                spec["every"] = _normalize_every(every)
            if target is not None:
                spec["target"] = _truncate(target, 40) or self.default_target
            if active_start is not None:
                spec.setdefault("active_hours", {})["start"] = _truncate(active_start, 5)
            if active_end is not None:
                spec.setdefault("active_hours", {})["end"] = _truncate(active_end, 5)
            if paused is not None:
                spec["paused"] = bool(paused)
            spec["updated_at"] = _now_iso()
            self.heartbeat_path(user_id).write_text(
                self._render_markdown(spec, checklist), encoding="utf-8"
            )
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
            normalized = self._normalize_spec(user_id, spec)
            normalized["checklist"] = checklist
            return normalized

    async def list_checklist(self, user_id: str) -> List[str]:
        state = await self.get_state(user_id)
        return list(state["checklist"])

    async def add_checklist_item(self, user_id: str, item: str) -> List[str]:
        normalized = _truncate(item, 400)
        if not normalized:
            return await self.list_checklist(user_id)
        async with self._user_lock(user_id):
            spec, checklist, status = self._ensure_user_files_unlocked(user_id)
            if normalized not in checklist:
                checklist.append(normalized)
                spec["updated_at"] = _now_iso()
                self.heartbeat_path(user_id).write_text(
                    self._render_markdown(spec, checklist), encoding="utf-8"
                )
                status["last_update"] = _now_iso()
                self._write_status_unlocked(user_id, status)
            return checklist

    async def remove_checklist_item(self, user_id: str, index: int) -> List[str]:
        async with self._user_lock(user_id):
            spec, checklist, status = self._ensure_user_files_unlocked(user_id)
            if 1 <= index <= len(checklist):
                checklist.pop(index - 1)
                spec["updated_at"] = _now_iso()
                self.heartbeat_path(user_id).write_text(
                    self._render_markdown(spec, checklist), encoding="utf-8"
                )
                status["last_update"] = _now_iso()
                self._write_status_unlocked(user_id, status)
            return checklist

    async def mark_heartbeat_run(
        self,
        user_id: str,
        result: str,
        *,
        run_at: str | None = None,
    ) -> Dict[str, Any]:
        stamp = run_at or _now_iso()
        async with self._user_lock(user_id):
            spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            every_sec = _parse_every_seconds(spec.get("every", self.default_every))
            run_dt = _parse_iso(stamp) or _now_local()
            next_due = (run_dt + timedelta(seconds=every_sec)).isoformat(timespec="seconds")
            level = self.classify_result(result)

            heartbeat = status.get("heartbeat") or {}
            heartbeat["last_run_at"] = stamp
            heartbeat["last_result"] = _truncate(result, 4000)
            heartbeat["next_due_at"] = next_due
            heartbeat["last_level"] = level
            status["heartbeat"] = heartbeat
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
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
        """
        CLEAN-001:
        Remove pathological nesting like worker::a::worker::a::123 by
        canonicalizing user keys to the right-most segment after `::`.
        """
        if not self.root.exists():
            return 0
        moved = 0
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            name = child.name.strip()
            if "::" not in name:
                continue
            canonical = name.split("::")[-1].strip()
            if not canonical or canonical == name:
                continue
            target = (self.root / canonical).resolve()
            target.mkdir(parents=True, exist_ok=True)
            for filename in ("HEARTBEAT.md", "STATUS.json", "HEARTBEAT.v1.bak.md"):
                src = (child / filename).resolve()
                dst = (target / filename).resolve()
                if src.exists() and not dst.exists():
                    dst.write_bytes(src.read_bytes())
            try:
                if not any(child.iterdir()):
                    child.rmdir()
            except Exception:
                pass
            moved += 1
        return moved

    async def get_delivery_target(self, user_id: str) -> Dict[str, str]:
        state = await self.get_state(user_id)
        delivery = state["status"].get("delivery") or {}
        return {
            "platform": str(delivery.get("last_platform", "")).strip(),
            "chat_id": str(delivery.get("last_chat_id", "")).strip(),
        }

    async def set_delivery_target(self, user_id: str, platform: str, chat_id: str) -> None:
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            delivery = status.get("delivery") or {}
            delivery["last_platform"] = _truncate(platform, 64)
            delivery["last_chat_id"] = _truncate(chat_id, 128)
            status["delivery"] = delivery
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)

    async def get_session_active_task(self, user_id: str) -> Dict[str, Any] | None:
        state = await self.get_state(user_id)
        task = (state["status"].get("session") or {}).get("active_task")
        return self._normalize_active_task(task)

    async def set_session_active_task(self, user_id: str, task: Dict[str, Any]) -> Dict[str, Any] | None:
        normalized = self._normalize_active_task(task)
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            session = status.get("session") or {}
            session["active_task"] = normalized
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
            return normalized

    async def update_session_active_task(self, user_id: str, **fields) -> Dict[str, Any] | None:
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            session = status.get("session") or {}
            current = self._normalize_active_task(session.get("active_task"))
            if current is None:
                return None
            for key in (
                "goal",
                "status",
                "source",
                "result_summary",
                "needs_confirmation",
                "confirmation_deadline",
            ):
                if key in fields:
                    current[key] = fields[key]
            current["updated_at"] = _now_iso()
            terminal_statuses = {"done", "failed", "cancelled", "timed_out"}
            current_status = str(current.get("status", "")).strip().lower()
            should_clear = bool(fields.get("clear_active")) or current_status in terminal_statuses
            if should_clear:
                session["active_task"] = None
            else:
                session["active_task"] = self._normalize_active_task(current)
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
            return self._normalize_active_task(session.get("active_task"))

    async def clear_session_active_task(self, user_id: str) -> None:
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            session = status.get("session") or {}
            session["active_task"] = None
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)

    async def append_session_event(self, user_id: str, message: str) -> None:
        note = _truncate(message.replace("\r", " ").replace("\n", " ").strip(), 800)
        if not note:
            return
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            session = status.get("session") or {}
            events = session.get("events")
            if not isinstance(events, list):
                events = []
            stamped = f"{_now_iso()} | {note}"
            events.append(stamped)
            session["events"] = events[-self.session_event_keep :]
            session["last_event"] = stamped
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)

    async def get_active_worker_id(self, user_id: str) -> str:
        state = await self.get_state(user_id)
        session = state["status"].get("session") or {}
        worker_id = str(session.get("active_worker_id", "worker-main")).strip()
        return worker_id or "worker-main"

    async def set_active_worker_id(self, user_id: str, worker_id: str) -> str:
        safe = _truncate(worker_id, 80) or "worker-main"
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            session = status.get("session") or {}
            session["active_worker_id"] = safe
            status["session"] = session
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
            return safe

    async def pulse(self, user_id: str, note: str = "") -> None:
        if note:
            await self.append_session_event(user_id, f"pulse: {note}")
        else:
            async with self._user_lock(user_id):
                _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
                status["last_update"] = _now_iso()
                self._write_status_unlocked(user_id, status)

    async def claim_lock(self, user_id: str, owner: str, ttl_sec: int | None = None) -> bool:
        lock_ttl = max(1, int(ttl_sec or self.lock_timeout_sec))
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
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
            self._write_status_unlocked(user_id, status)
            return True

    async def refresh_lock(self, user_id: str, owner: str, ttl_sec: int | None = None) -> bool:
        lock_ttl = max(1, int(ttl_sec or self.lock_timeout_sec))
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            if str(status.get("locked_by", "")).strip() != str(owner):
                return False
            status["lock_expires_at"] = (
                _now_local() + timedelta(seconds=lock_ttl)
            ).isoformat(timespec="seconds")
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
            return True

    async def release_lock(self, user_id: str, owner: str | None = None) -> bool:
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            current_owner = str(status.get("locked_by", "")).strip()
            if owner and current_owner and current_owner != str(owner):
                return False
            status["locked_by"] = ""
            status["lock_expires_at"] = ""
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)
            return True

    async def set_last_error(self, user_id: str, error: str) -> None:
        async with self._user_lock(user_id):
            _spec, _checklist, status = self._ensure_user_files_unlocked(user_id)
            status["last_error"] = _truncate(error, 1000)
            status["last_update"] = _now_iso()
            self._write_status_unlocked(user_id, status)

    async def clear_last_error(self, user_id: str) -> None:
        await self.set_last_error(user_id, "")

    def _resolve_now_for_spec(self, spec: Dict[str, Any]) -> datetime:
        tz_name = self.default_timezone
        if ZoneInfo and tz_name:
            try:
                return datetime.now(ZoneInfo(tz_name))
            except Exception:
                return _now_local()
        return _now_local()

    def _is_in_active_hours(self, spec: Dict[str, Any], now_dt: datetime) -> bool:
        active = spec.get("active_hours") or {}
        start_t = _parse_hhmm(str(active.get("start", self.default_active_start)), self.default_active_start)
        end_t = _parse_hhmm(str(active.get("end", self.default_active_end)), self.default_active_end)
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
        last_run_at = ((status.get("heartbeat") or {}).get("last_run_at", "")).strip()
        last_run = _parse_iso(last_run_at)
        if last_run is None:
            return True
        return (now_dt - last_run).total_seconds() >= every_sec


heartbeat_store = HeartbeatStore()
