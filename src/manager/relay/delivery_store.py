from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import DATA_DIR
from shared.contracts.dispatch import TaskEnvelope


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime | None:
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
    if token in {"interactive", "background"}:
        return token
    return "interactive"


def _normalize_status(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"pending", "retrying", "delivered", "dead_letter", "suppressed"}:
        return token
    return "pending"


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


@dataclass
class DeliveryJob:
    job_id: str
    task_id: str
    worker_id: str = ""
    session_task_id: str = ""
    task_inbox_id: str = ""
    user_id: str = ""
    source: str = "worker_result"
    priority: str = "interactive"
    body_mode: str = "auto"
    target_platform: str = ""
    target_chat_id: str = ""
    status: str = "pending"
    attempts: int = 0
    last_error: str = ""
    next_retry_at: str = ""
    delivered_at: str = ""
    last_summary: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeliveryStore:
    def __init__(self) -> None:
        self.root = (Path(os.getenv("DATA_DIR", DATA_DIR)) / "system" / "delivery").resolve()
        self.jobs_root = (self.root / "jobs").resolve()
        self.events_path = (self.root / "events.jsonl").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        if not self.events_path.exists():
            self.events_path.write_text("", encoding="utf-8")
        self._lock = asyncio.Lock()
        self._loaded = False
        self._jobs: Dict[str, DeliveryJob] = {}

    def _job_path(self, task_id: str) -> Path:
        safe_task_id = _safe_text(task_id, limit=120)
        return (self.jobs_root / f"{safe_task_id}.json").resolve()

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            loaded: Dict[str, DeliveryJob] = {}
            for path in sorted(self.jobs_root.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        continue
                    job = DeliveryJob(**raw)
                    job.priority = _normalize_priority(job.priority)
                    job.status = _normalize_status(job.status)
                    loaded[job.task_id] = job
                except Exception:
                    continue
            self._jobs = loaded
            self._loaded = True

    async def _persist_job_unlocked(self, job: DeliveryJob) -> None:
        path = self._job_path(job.task_id)
        path.write_text(
            json.dumps(job.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def _append_event_unlocked(self, task_id: str, event: str, detail: str = "") -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "at": _now_iso(),
                        "task_id": _safe_text(task_id, limit=120),
                        "event": _safe_text(event, limit=80) or "event",
                        "detail": _safe_text(detail, limit=400),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    async def ensure_job(
        self,
        *,
        task: TaskEnvelope,
        source: str = "",
        priority: str = "interactive",
        body_mode: str = "auto",
        target_platform: str = "",
        target_chat_id: str = "",
    ) -> DeliveryJob:
        await self._ensure_loaded()
        task_id = _safe_text(task.task_id, limit=120)
        metadata = dict(task.metadata or {})
        async with self._lock:
            existing = self._jobs.get(task_id)
            if existing is not None:
                changed = False
                if target_platform and existing.target_platform != target_platform:
                    existing.target_platform = target_platform
                    changed = True
                if target_chat_id and existing.target_chat_id != target_chat_id:
                    existing.target_chat_id = target_chat_id
                    changed = True
                if changed:
                    existing.updated_at = _now_iso()
                    await self._persist_job_unlocked(existing)
                return existing

            job = DeliveryJob(
                job_id=f"dlv-{task_id}",
                task_id=task_id,
                worker_id=_safe_text(task.worker_id, limit=80),
                session_task_id=_safe_text(metadata.get("session_task_id"), limit=80),
                task_inbox_id=_safe_text(metadata.get("task_inbox_id"), limit=80),
                user_id=_safe_text(metadata.get("user_id"), limit=80),
                source=_safe_text(source or task.source or "worker_result", limit=80)
                or "worker_result",
                priority=_normalize_priority(priority),
                body_mode=_safe_text(body_mode, limit=40) or "auto",
                target_platform=_safe_text(target_platform, limit=40),
                target_chat_id=_safe_text(target_chat_id, limit=128),
                metadata={
                    "worker_name": _safe_text(metadata.get("worker_name"), limit=120),
                    "task_source": _safe_text(task.source, limit=80),
                },
            )
            self._jobs[job.task_id] = job
            await self._persist_job_unlocked(job)
            await self._append_event_unlocked(job.task_id, "created", job.priority)
            return job

    async def get(self, task_id: str) -> Optional[DeliveryJob]:
        await self._ensure_loaded()
        safe_task_id = _safe_text(task_id, limit=120)
        async with self._lock:
            return self._jobs.get(safe_task_id)

    async def list_ready(self, *, limit: int = 20) -> List[DeliveryJob]:
        await self._ensure_loaded()
        now = datetime.now().astimezone()
        async with self._lock:
            rows: List[DeliveryJob] = []
            for job in self._jobs.values():
                if job.status not in {"pending", "retrying"}:
                    continue
                retry_at = _parse_iso(job.next_retry_at)
                if retry_at is not None and retry_at > now:
                    continue
                rows.append(job)
            rows.sort(
                key=lambda item: (
                    0 if item.priority == "interactive" else 1,
                    item.created_at,
                )
            )
            return rows[: max(1, int(limit or 1))]

    async def schedule_retry(
        self,
        task_id: str,
        *,
        reason: str,
        retry_after_sec: float,
        max_retries: int,
    ) -> Dict[str, Any] | None:
        await self._ensure_loaded()
        safe_task_id = _safe_text(task_id, limit=120)
        safe_reason = _safe_text(reason, limit=200) or "delivery_failed"
        safe_max_retries = max(1, int(max_retries or 1))
        safe_retry_after = max(0.0, float(retry_after_sec or 0.0))
        async with self._lock:
            job = self._jobs.get(safe_task_id)
            if job is None:
                return None
            job.attempts = max(0, int(job.attempts or 0)) + 1
            now = datetime.now().astimezone()
            if job.attempts >= safe_max_retries:
                job.status = "dead_letter"
                job.next_retry_at = ""
            else:
                job.status = "retrying"
                job.next_retry_at = (
                    now + timedelta(seconds=safe_retry_after)
                ).isoformat(timespec="seconds")
            job.last_error = safe_reason
            job.updated_at = now.isoformat(timespec="seconds")
            await self._persist_job_unlocked(job)
            await self._append_event_unlocked(job.task_id, job.status, safe_reason)
            return {
                "task_id": job.task_id,
                "state": job.status,
                "attempts": job.attempts,
                "next_retry_at": job.next_retry_at,
                "last_error": job.last_error,
            }

    async def mark_delivered(self, task_id: str, *, summary: str = "") -> bool:
        await self._ensure_loaded()
        safe_task_id = _safe_text(task_id, limit=120)
        async with self._lock:
            job = self._jobs.get(safe_task_id)
            if job is None:
                return False
            job.status = "delivered"
            job.delivered_at = _now_iso()
            job.updated_at = job.delivered_at
            if summary:
                job.last_summary = _safe_text(summary, limit=3000)
            await self._persist_job_unlocked(job)
            await self._append_event_unlocked(job.task_id, "delivered", job.last_summary)
            return True

    async def mark_suppressed(
        self,
        task_id: str,
        *,
        reason: str,
        summary: str = "",
    ) -> bool:
        await self._ensure_loaded()
        safe_task_id = _safe_text(task_id, limit=120)
        safe_reason = _safe_text(reason, limit=200) or "suppressed"
        async with self._lock:
            job = self._jobs.get(safe_task_id)
            if job is None:
                return False
            job.status = "suppressed"
            job.updated_at = _now_iso()
            job.last_error = safe_reason
            if summary:
                job.last_summary = _safe_text(summary, limit=3000)
            await self._persist_job_unlocked(job)
            await self._append_event_unlocked(job.task_id, "suppressed", safe_reason)
            return True

    async def delivery_health(
        self,
        *,
        worker_id: str = "",
        dead_letter_limit: int = 20,
    ) -> Dict[str, Any]:
        await self._ensure_loaded()
        safe_worker_id = _safe_text(worker_id, limit=80)
        safe_limit = max(1, min(100, int(dead_letter_limit or 20)))
        async with self._lock:
            undelivered = 0
            retrying = 0
            dead_letter = 0
            delivered = 0
            latency_samples: list[float] = []
            oldest_undelivered_age_sec = 0.0
            dead_rows: List[Dict[str, Any]] = []
            now = datetime.now().astimezone()
            for job in self._jobs.values():
                if safe_worker_id and _safe_text(job.worker_id, limit=80) != safe_worker_id:
                    continue
                if job.status == "pending":
                    undelivered += 1
                    created_at = _parse_iso(job.created_at)
                    if created_at is not None:
                        oldest_undelivered_age_sec = max(
                            oldest_undelivered_age_sec,
                            max(0.0, (now - created_at).total_seconds()),
                        )
                elif job.status == "retrying":
                    undelivered += 1
                    retrying += 1
                    created_at = _parse_iso(job.created_at)
                    if created_at is not None:
                        oldest_undelivered_age_sec = max(
                            oldest_undelivered_age_sec,
                            max(0.0, (now - created_at).total_seconds()),
                        )
                elif job.status == "dead_letter":
                    undelivered += 1
                    dead_letter += 1
                    created_at = _parse_iso(job.created_at)
                    if created_at is not None:
                        oldest_undelivered_age_sec = max(
                            oldest_undelivered_age_sec,
                            max(0.0, (now - created_at).total_seconds()),
                        )
                    dead_rows.append(
                        {
                            "task_id": job.task_id,
                            "worker_id": job.worker_id,
                            "source": job.source,
                            "updated_at": job.updated_at,
                            "attempts": job.attempts,
                            "last_error": job.last_error,
                            "next_retry_at": job.next_retry_at,
                            "dead_letter_at": job.updated_at,
                        }
                    )
                elif job.status == "delivered":
                    delivered += 1
                    created_at = _parse_iso(job.created_at)
                    delivered_at = _parse_iso(job.delivered_at)
                    if created_at is not None and delivered_at is not None:
                        latency_samples.append(
                            max(0.0, (delivered_at - created_at).total_seconds())
                        )
            dead_rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            avg_delivery_latency_sec = (
                sum(latency_samples) / len(latency_samples) if latency_samples else 0.0
            )
            max_delivery_latency_sec = max(latency_samples) if latency_samples else 0.0
            return {
                "worker_id": safe_worker_id,
                "undelivered": undelivered,
                "retrying": retrying,
                "dead_letter": dead_letter,
                "delivered": delivered,
                "avg_delivery_latency_sec": round(avg_delivery_latency_sec, 3),
                "max_delivery_latency_sec": round(max_delivery_latency_sec, 3),
                "oldest_undelivered_age_sec": round(oldest_undelivered_age_sec, 3),
                "result_persist_error": 0,
                "recent_dead_letters": dead_rows[:safe_limit],
            }

    async def requeue_dead_letter(
        self,
        *,
        task_id: str,
        reason: str = "manual_requeue",
    ) -> Dict[str, Any]:
        await self._ensure_loaded()
        safe_task_id = _safe_text(task_id, limit=120)
        if not safe_task_id:
            return {
                "ok": False,
                "task_id": "",
                "retried": False,
                "summary": "task_id is required",
            }
        async with self._lock:
            job = self._jobs.get(safe_task_id)
            if job is None:
                return {
                    "ok": False,
                    "task_id": safe_task_id,
                    "retried": False,
                    "summary": "delivery job not found",
                }
            if job.status != "dead_letter":
                return {
                    "ok": False,
                    "task_id": safe_task_id,
                    "retried": False,
                    "summary": "delivery job is not in dead_letter state",
                }
            job.status = "pending"
            job.next_retry_at = ""
            job.last_error = _safe_text(reason, limit=200) or "manual_requeue"
            job.updated_at = _now_iso()
            await self._persist_job_unlocked(job)
            await self._append_event_unlocked(job.task_id, "requeued", job.last_error)
            return {
                "ok": True,
                "task_id": safe_task_id,
                "retried": True,
                "summary": "dead-letter delivery job requeued",
            }


delivery_store = DeliveryStore()
