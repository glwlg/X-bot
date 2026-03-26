import asyncio
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.config import DATA_DIR

logger = logging.getLogger(__name__)

OPEN_TASK_STATUSES = {
    "pending",
    "planning",
    "running",
    "waiting_user",
    "waiting_external",
}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _normalize_priority(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"high", "normal", "low"}:
        return token
    return "normal"


def _normalize_status(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in OPEN_TASK_STATUSES | TERMINAL_TASK_STATUSES:
        return token
    return "pending"


def _merge_dict(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for key, value in dict(updates or {}).items():
        safe_key = str(key)
        if isinstance(merged.get(safe_key), dict) and isinstance(value, dict):
            merged[safe_key] = _merge_dict(
                dict(merged.get(safe_key) or {}),
                dict(value),
            )
        else:
            merged[safe_key] = value
    return merged


def _normalize_output_payload(
    output: Any,
    *,
    final_output: str = "",
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    if isinstance(output, dict):
        normalized.update(output)

    result_dict = dict(result or {})
    payload_dict = result_dict.get("payload")
    if isinstance(payload_dict, dict):
        for key, value in payload_dict.items():
            normalized.setdefault(str(key), value)

    text_candidates = [
        normalized.get("text"),
        str(final_output or "").strip(),
        result_dict.get("text"),
        result_dict.get("result"),
        result_dict.get("message"),
        result_dict.get("summary"),
    ]
    for value in text_candidates:
        text = str(value or "").strip()
        if text:
            normalized["text"] = text
            break

    ui_candidate = normalized.get("ui")
    if not isinstance(ui_candidate, dict):
        ui_candidate = result_dict.get("ui")
    if not isinstance(ui_candidate, dict) and isinstance(payload_dict, dict):
        maybe_ui = payload_dict.get("ui")
        if isinstance(maybe_ui, dict):
            ui_candidate = maybe_ui
    if isinstance(ui_candidate, dict):
        normalized["ui"] = ui_candidate

    if "error" not in normalized:
        error_text = str(result_dict.get("error") or "").strip()
        if error_text:
            normalized["error"] = error_text

    return normalized


def _task_session_id(task: "TaskEnvelope") -> str:
    for source in (task.metadata, task.payload):
        if not isinstance(source, dict):
            continue
        session_id = str(source.get("session_id") or "").strip()
        if session_id:
            return session_id
    return ""


def _task_output_text(task: "TaskEnvelope") -> str:
    for candidate in (
        task.final_output,
        dict(task.output or {}).get("text"),
        dict(task.result or {}).get("summary"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


async def _sync_completed_user_chat_to_session(task: "TaskEnvelope") -> None:
    if str(task.source or "").strip().lower() != "user_chat":
        return
    if not bool(task.requires_reply):
        return

    safe_user_id = str(task.user_id or "").strip()
    session_id = _task_session_id(task)
    text = _task_output_text(task)
    if not safe_user_id or not session_id or not text:
        return

    try:
        from core.state_store import get_session_entries, save_message

        rows = await get_session_entries(safe_user_id, session_id)
        tail = rows[-4:]
        if any(
            str(item.get("role") or "").strip().lower() == "model"
            and str(item.get("content") or "").strip() == text
            for item in tail
        ):
            return
        await save_message(safe_user_id, "model", text, session_id)
    except Exception:
        logger.debug(
            "Failed to sync completed task into chat history task=%s user=%s session=%s",
            task.task_id,
            safe_user_id,
            session_id,
            exc_info=True,
        )


@dataclass
class TaskEnvelope:
    task_id: str = field(default_factory=lambda: str(uuid4()))
    source: str = "system"
    goal: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    user_id: str = ""
    requires_reply: bool = True
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    status: str = "pending"
    executor_id: str = ""
    assignment_reason: str = ""
    ikaros_id: str = "core-ikaros"
    result: Dict[str, Any] = field(default_factory=dict)
    final_output: str = ""
    output: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def add_event(
        self, event: str, detail: str = "", extra: Optional[Dict[str, Any]] = None
    ) -> None:
        payload = {
            "at": _now_iso(),
            "event": str(event or "").strip() or "event",
            "detail": str(detail or "").strip(),
            "extra": dict(extra or {}),
        }
        self.events.append(payload)
        self.updated_at = payload["at"]


class TaskInbox:
    """Unified inbox for ikaros-facing tasks."""

    def __init__(self) -> None:
        self.persist = os.getenv("TASK_INBOX_PERSIST", "true").strip().lower() == "true"
        self.clean_on_start = (
            os.getenv("TASK_INBOX_CLEAN_ON_START", "false").strip().lower() == "true"
        )
        self.global_event_log_enabled = (
            os.getenv("TASK_INBOX_GLOBAL_EVENT_LOG_ENABLED", "false").strip().lower()
            == "true"
        )
        try:
            self.max_events_per_task = int(
                os.getenv("TASK_INBOX_MAX_EVENTS_PER_TASK", "50")
            )
        except Exception:
            self.max_events_per_task = 50
        try:
            self.completed_keep_limit = int(
                os.getenv("TASK_INBOX_COMPLETED_KEEP_COUNT", "10")
            )
        except Exception:
            self.completed_keep_limit = 10
        self.max_events_per_task = max(1, self.max_events_per_task)
        self.completed_keep_limit = max(0, self.completed_keep_limit)

        self.root = (Path(DATA_DIR) / "task_inbox").resolve()
        self.tasks_root = (self.root / "tasks").resolve()
        self.archive_root = (self.root / "archive").resolve()
        self.events_path = (self.root / "events.jsonl").resolve()
        if self.persist:
            if self.clean_on_start and self.root.exists():
                shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            self.tasks_root.mkdir(parents=True, exist_ok=True)
            self.archive_root.mkdir(parents=True, exist_ok=True)
            if self.global_event_log_enabled and not self.events_path.exists():
                self.events_path.write_text("", encoding="utf-8")

        self._lock = asyncio.Lock()
        self._loaded = False
        self._tasks: Dict[str, TaskEnvelope] = {}
        self._startup_maintenance_done = False

    def _task_path(self, task_id: str) -> Path:
        safe = str(task_id or "").strip()
        return (self.tasks_root / f"{safe}.json").resolve()

    def _legacy_events_archive_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return (self.archive_root / f"legacy-events-{stamp}.jsonl").resolve()

    @staticmethod
    def _is_open_task(task: TaskEnvelope) -> bool:
        return str(task.status or "").strip().lower() in OPEN_TASK_STATUSES

    @staticmethod
    def _is_terminal_task(task: TaskEnvelope) -> bool:
        return str(task.status or "").strip().lower() in TERMINAL_TASK_STATUSES

    @staticmethod
    def _resume_window_active(task: TaskEnvelope) -> bool:
        metadata = dict(task.metadata or {})
        resume_until = _parse_iso(metadata.get("resume_window_until"))
        if resume_until is None:
            return False
        return resume_until > datetime.now().astimezone()

    def _trim_task_events(self, task: TaskEnvelope) -> bool:
        events = [item for item in list(task.events or []) if isinstance(item, dict)]
        if len(events) <= self.max_events_per_task:
            task.events = events
            return False
        task.events = events[-self.max_events_per_task :]
        return True

    async def _delete_task_unlocked(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        try:
            self._task_path(task_id).unlink()
        except FileNotFoundError:
            return
        except Exception:
            logger.debug("Failed to delete task file task_id=%s", task_id, exc_info=True)

    async def _archive_legacy_events_unlocked(self) -> None:
        if self.global_event_log_enabled or not self.events_path.exists():
            return
        try:
            if self.events_path.stat().st_size <= 0:
                self.events_path.unlink(missing_ok=True)
                return
        except FileNotFoundError:
            return
        except Exception:
            logger.debug("Failed to stat legacy task events log", exc_info=True)
            return

        target = self._legacy_events_archive_path()
        try:
            self.archive_root.mkdir(parents=True, exist_ok=True)
            self.events_path.replace(target)
        except Exception:
            logger.debug("Failed to archive legacy task events log", exc_info=True)

    async def _compact_tasks_unlocked(self) -> set[str]:
        deleted_ids: set[str] = set()
        pinned_terminal_ids = {
            task.task_id
            for task in self._tasks.values()
            if self._is_terminal_task(task) and self._resume_window_active(task)
        }

        for task in list(self._tasks.values()):
            if not self._is_terminal_task(task):
                continue
            if task.task_id in pinned_terminal_ids:
                continue
            if str(task.source or "").strip().lower() == "heartbeat":
                deleted_ids.add(task.task_id)
                await self._delete_task_unlocked(task.task_id)

        candidates = [
            task
            for task in self._tasks.values()
            if self._is_terminal_task(task)
            and task.task_id not in pinned_terminal_ids
            and str(task.source or "").strip().lower() != "heartbeat"
        ]
        candidates.sort(
            key=lambda item: (
                str(item.updated_at or ""),
                str(item.created_at or ""),
                str(item.task_id or ""),
            ),
            reverse=True,
        )
        for task in candidates[self.completed_keep_limit :]:
            deleted_ids.add(task.task_id)
            await self._delete_task_unlocked(task.task_id)
        return deleted_ids

    async def _run_maintenance_unlocked(self) -> None:
        dirty_ids: set[str] = set()
        for task in self._tasks.values():
            if self._trim_task_events(task):
                dirty_ids.add(task.task_id)
        await self._archive_legacy_events_unlocked()
        deleted_ids = await self._compact_tasks_unlocked()
        for task_id in dirty_ids - deleted_ids:
            task = self._tasks.get(task_id)
            if task is None:
                continue
            await self._persist_task_unlocked(task)

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self.persist:
            self._loaded = True
            return
        async with self._lock:
            if self._loaded:
                return
            loaded: Dict[str, TaskEnvelope] = {}
            for path in sorted(self.tasks_root.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        continue
                    task = TaskEnvelope(**raw)
                    task.priority = _normalize_priority(task.priority)
                    task.status = _normalize_status(task.status)
                    task.output = _normalize_output_payload(
                        task.output,
                        final_output=task.final_output,
                        result=task.result,
                    )
                    self._trim_task_events(task)
                    loaded[task.task_id] = task
                except Exception:
                    continue
            self._tasks = loaded
            await self._run_maintenance_unlocked()
            self._startup_maintenance_done = True
            self._loaded = True

    async def _persist_task_unlocked(self, task: TaskEnvelope) -> None:
        if not self.persist:
            return
        self._trim_task_events(task)
        path = self._task_path(task.task_id)
        path.write_text(
            json.dumps(task.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def _append_log_unlocked(
        self, task_id: str, event: str, detail: str = ""
    ) -> None:
        if not self.persist or not self.global_event_log_enabled:
            return
        entry = {
            "at": _now_iso(),
            "task_id": str(task_id or "").strip(),
            "event": str(event or "").strip() or "event",
            "detail": str(detail or "").strip(),
        }
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def compact_storage(self) -> None:
        await self._ensure_loaded()
        if not self.persist:
            return
        async with self._lock:
            await self._run_maintenance_unlocked()

    async def submit(
        self,
        *,
        source: str,
        goal: str,
        user_id: str | int,
        payload: Optional[Dict[str, Any]] = None,
        priority: str = "normal",
        requires_reply: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskEnvelope:
        await self._ensure_loaded()
        task = TaskEnvelope(
            source=str(source or "system").strip().lower() or "system",
            goal=str(goal or "").strip(),
            payload=dict(payload or {}),
            priority=_normalize_priority(priority),
            user_id=str(user_id or "").strip(),
            requires_reply=bool(requires_reply),
            metadata=dict(metadata or {}),
        )
        task.status = "pending"
        task.add_event("submitted", detail=task.goal[:180])

        async with self._lock:
            self._tasks[task.task_id] = task
            await self._persist_task_unlocked(task)
            await self._append_log_unlocked(task.task_id, "submitted", task.goal[:180])
        return task

    async def get(self, task_id: str) -> Optional[TaskEnvelope]:
        await self._ensure_loaded()
        key = str(task_id or "").strip()
        if not key:
            return None
        async with self._lock:
            task = self._tasks.get(key)
            return task if task is not None else None

    async def delete(self, task_id: str) -> bool:
        await self._ensure_loaded()
        key = str(task_id or "").strip()
        if not key:
            return False
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return False
            await self._delete_task_unlocked(key)
            return True

    async def list_pending(
        self,
        *,
        user_id: str | int | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> List[TaskEnvelope]:
        await self._ensure_loaded()
        uid = str(user_id or "").strip() if user_id is not None else ""
        source_norm = str(source or "").strip().lower() if source is not None else ""
        async with self._lock:
            rows = []
            for task in self._tasks.values():
                if task.status != "pending":
                    continue
                if uid and task.user_id != uid:
                    continue
                if source_norm and task.source != source_norm:
                    continue
                rows.append(task)
            rows.sort(
                key=lambda item: (
                    0
                    if item.priority == "high"
                    else 1
                    if item.priority == "normal"
                    else 2,
                    item.created_at,
                )
            )
            return rows[: max(1, int(limit or 1))]

    async def list_recent(
        self,
        *,
        user_id: str | int | None = None,
        limit: int = 30,
    ) -> List[TaskEnvelope]:
        await self._ensure_loaded()
        uid = str(user_id or "").strip() if user_id is not None else ""
        async with self._lock:
            rows = []
            for task in self._tasks.values():
                if uid and task.user_id != uid:
                    continue
                rows.append(task)
            rows.sort(key=lambda item: item.updated_at, reverse=True)
            return rows[: max(1, int(limit or 1))]

    async def list_recent_outputs(
        self,
        *,
        user_id: str | int | None = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        rows = await self.list_recent(user_id=user_id, limit=limit)
        return [
            {
                "task_id": item.task_id,
                "source": item.source,
                "status": item.status,
                "updated_at": item.updated_at,
                "output": dict(item.output or {}),
            }
            for item in rows
        ]

    async def list_open(
        self,
        *,
        user_id: str | int | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> List[TaskEnvelope]:
        await self._ensure_loaded()
        uid = str(user_id or "").strip() if user_id is not None else ""
        source_norm = str(source or "").strip().lower() if source is not None else ""
        async with self._lock:
            rows = []
            for task in self._tasks.values():
                if task.status not in OPEN_TASK_STATUSES:
                    continue
                if uid and task.user_id != uid:
                    continue
                if source_norm and task.source != source_norm:
                    continue
                rows.append(task)
            rows.sort(key=lambda item: item.updated_at, reverse=True)
            rows.sort(
                key=lambda item: (
                    0
                    if item.priority == "high"
                    else 1
                    if item.priority == "normal"
                    else 2
                )
            )
            safe_limit = int(limit or 0)
            if safe_limit <= 0:
                return rows
            return rows[:safe_limit]

    async def update_status(
        self,
        task_id: str,
        status: str,
        *,
        event: str = "status_updated",
        detail: str = "",
        **fields: Any,
    ) -> bool:
        await self._ensure_loaded()
        key = str(task_id or "").strip()
        if not key:
            return False
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return False
            task.status = _normalize_status(status)
            for name, value in fields.items():
                if hasattr(task, name):
                    if name in {"metadata", "result", "output"} and isinstance(
                        value, dict
                    ):
                        current_value = getattr(task, name, {})
                        current_obj = (
                            dict(current_value)
                            if isinstance(current_value, dict)
                            else {}
                        )
                        setattr(task, name, _merge_dict(current_obj, dict(value)))
                    else:
                        setattr(task, name, value)
            task.output = _normalize_output_payload(
                task.output,
                final_output=task.final_output,
                result=task.result,
            )
            task.updated_at = _now_iso()
            task.add_event(event, detail=detail)
            await self._persist_task_unlocked(task)
            await self._append_log_unlocked(task.task_id, event, detail)
            await self._run_maintenance_unlocked()
            return True

    async def assign_executor(
        self,
        task_id: str,
        *,
        executor_id: str,
        reason: str = "",
        ikaros_id: str = "core-ikaros",
    ) -> bool:
        return await self.update_status(
            task_id,
            "running",
            event="executor_assigned",
            detail=f"executor={executor_id}; reason={reason[:120]}",
            executor_id=str(executor_id or "").strip(),
            assignment_reason=str(reason or "").strip(),
            ikaros_id=str(ikaros_id or "core-ikaros").strip() or "core-ikaros",
        )

    async def complete(
        self,
        task_id: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        final_output: str = "",
        output: Optional[Dict[str, Any]] = None,
    ) -> bool:
        result_dict = dict(result or {})
        output_payload = _normalize_output_payload(
            output,
            final_output=str(final_output or ""),
            result=result_dict,
        )
        ok = await self.update_status(
            task_id,
            "completed",
            event="completed",
            detail=(str(final_output or "").strip()[:200]),
            result=result_dict,
            final_output=str(final_output or ""),
            output=output_payload,
        )
        if not ok:
            return False
        task = await self.get(task_id)
        if task is not None:
            await _sync_completed_user_chat_to_session(task)
        return True

    async def fail(
        self,
        task_id: str,
        *,
        error: str,
        result: Optional[Dict[str, Any]] = None,
        output: Optional[Dict[str, Any]] = None,
    ) -> bool:
        payload = dict(result or {})
        if "error" not in payload:
            payload["error"] = str(error or "failed")
        output_payload = _normalize_output_payload(
            output,
            final_output="",
            result=payload,
        )
        if "text" not in output_payload:
            output_payload["text"] = str(error or "failed").strip()
        return await self.update_status(
            task_id,
            "failed",
            event="failed",
            detail=str(error or "failed")[:200],
            result=payload,
            final_output="",
            output=output_payload,
        )


task_inbox = TaskInbox()
