import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.config import DATA_DIR


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_priority(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"high", "normal", "low"}:
        return token
    return "normal"


def _normalize_status(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"pending", "running", "completed", "failed", "cancelled"}:
        return token
    return "pending"


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
    assigned_worker_id: str = ""
    dispatch_reason: str = ""
    manager_id: str = "core-manager"
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
    """Unified inbox for all manager-facing tasks.

    Sources include: user_chat / heartbeat / cron / system.
    """

    def __init__(self) -> None:
        self.root = (Path(DATA_DIR) / "task_inbox").resolve()
        self.tasks_root = (self.root / "tasks").resolve()
        self.events_path = (self.root / "events.jsonl").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        if not self.events_path.exists():
            self.events_path.write_text("", encoding="utf-8")

        self._lock = asyncio.Lock()
        self._loaded = False
        self._tasks: Dict[str, TaskEnvelope] = {}

    def _task_path(self, task_id: str) -> Path:
        safe = str(task_id or "").strip()
        return (self.tasks_root / f"{safe}.json").resolve()

    async def _ensure_loaded(self) -> None:
        if self._loaded:
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
                    loaded[task.task_id] = task
                except Exception:
                    continue
            self._tasks = loaded
            self._loaded = True

    async def _persist_task_unlocked(self, task: TaskEnvelope) -> None:
        path = self._task_path(task.task_id)
        path.write_text(
            json.dumps(task.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def _append_log_unlocked(
        self, task_id: str, event: str, detail: str = ""
    ) -> None:
        entry = {
            "at": _now_iso(),
            "task_id": str(task_id or "").strip(),
            "event": str(event or "").strip() or "event",
            "detail": str(detail or "").strip(),
        }
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
            return True

    async def assign_worker(
        self,
        task_id: str,
        *,
        worker_id: str,
        reason: str = "",
        manager_id: str = "core-manager",
    ) -> bool:
        return await self.update_status(
            task_id,
            "running",
            event="worker_assigned",
            detail=f"worker={worker_id}; reason={reason[:120]}",
            assigned_worker_id=str(worker_id or "").strip(),
            dispatch_reason=str(reason or "").strip(),
            manager_id=str(manager_id or "core-manager").strip() or "core-manager",
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
        return await self.update_status(
            task_id,
            "completed",
            event="completed",
            detail=(str(final_output or "").strip()[:200]),
            result=result_dict,
            final_output=str(final_output or ""),
            output=output_payload,
        )

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
