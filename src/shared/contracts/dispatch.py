from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Literal, cast
from uuid import uuid4

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_task_id(prefix: str = "tsk") -> str:
    return f"{prefix}-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}"


@dataclass
class TaskEnvelope:
    task_id: str
    worker_id: str
    instruction: str
    source: str
    backend: str = ""
    priority: int = 0
    status: TaskStatus = "pending"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    started_at: str = ""
    ended_at: str = ""
    claimed_by: str = ""
    error: str = ""
    retry_count: int = 0
    delivered_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": str(self.task_id or "").strip(),
            "worker_id": str(self.worker_id or "").strip(),
            "instruction": str(self.instruction or "").strip(),
            "source": str(self.source or "manager_dispatch").strip(),
            "backend": str(self.backend or "").strip(),
            "priority": int(self.priority or 0),
            "status": str(self.status or "pending").strip().lower(),
            "metadata": dict(self.metadata or {}),
            "created_at": str(self.created_at or now_iso()),
            "updated_at": str(self.updated_at or now_iso()),
            "started_at": str(self.started_at or ""),
            "ended_at": str(self.ended_at or ""),
            "claimed_by": str(self.claimed_by or ""),
            "error": str(self.error or ""),
            "retry_count": max(0, int(self.retry_count or 0)),
            "delivered_at": str(self.delivered_at or ""),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TaskEnvelope":
        data = dict(payload or {})
        task_id = str(data.get("task_id") or "").strip() or new_task_id()
        worker_id = str(data.get("worker_id") or "worker-main").strip()
        instruction = str(data.get("instruction") or "").strip()
        source = str(data.get("source") or "manager_dispatch").strip()
        backend = str(data.get("backend") or "").strip()
        priority = int(data.get("priority") or 0)
        raw_status = str(data.get("status") or "pending").strip().lower()
        status: TaskStatus
        if raw_status in {"pending", "running", "done", "failed", "cancelled"}:
            status = cast(TaskStatus, raw_status)
        else:
            status = "pending"
        metadata = data.get("metadata")
        return cls(
            task_id=task_id,
            worker_id=worker_id,
            instruction=instruction,
            source=source,
            backend=backend,
            priority=priority,
            status=status,
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
            started_at=str(data.get("started_at") or ""),
            ended_at=str(data.get("ended_at") or ""),
            claimed_by=str(data.get("claimed_by") or ""),
            error=str(data.get("error") or ""),
            retry_count=max(0, int(data.get("retry_count") or 0)),
            delivered_at=str(data.get("delivered_at") or ""),
        )


@dataclass
class TaskResult:
    task_id: str
    worker_id: str
    ok: bool
    summary: str = ""
    error: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": str(self.task_id or "").strip(),
            "worker_id": str(self.worker_id or "").strip(),
            "ok": bool(self.ok),
            "summary": str(self.summary or "").strip(),
            "error": str(self.error or "").strip(),
            "payload": dict(self.payload or {}),
            "created_at": str(self.created_at or now_iso()),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TaskResult":
        data = dict(payload or {})
        raw_payload = data.get("payload")
        return cls(
            task_id=str(data.get("task_id") or "").strip(),
            worker_id=str(data.get("worker_id") or "").strip(),
            ok=bool(data.get("ok")),
            summary=str(data.get("summary") or "").strip(),
            error=str(data.get("error") or "").strip(),
            payload=dict(raw_payload) if isinstance(raw_payload, dict) else {},
            created_at=str(data.get("created_at") or now_iso()),
        )
