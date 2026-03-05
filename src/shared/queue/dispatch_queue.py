from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Tuple

from shared.contracts.dispatch import TaskEnvelope, TaskResult, new_task_id, now_iso
from shared.queue.jsonl_queue import FileLock, JsonlTable

logger = logging.getLogger(__name__)


def _dispatch_root() -> str:
    base_dir = os.getenv("DATA_DIR", "/app/data").strip() or "/app/data"
    default_root = os.path.join(base_dir, "system", "dispatch")
    return os.path.abspath(
        os.getenv("MANAGER_DISPATCH_ROOT", default_root).strip() or default_root
    )


class DispatchQueue:
    def __init__(self) -> None:
        root = _dispatch_root()
        self.tasks = JsonlTable(os.path.join(root, "tasks.jsonl"))
        self.results = JsonlTable(os.path.join(root, "results.jsonl"))

    @staticmethod
    def _claim_stale_after_sec() -> float:
        try:
            raw = float(os.getenv("DISPATCH_RUNNING_STALE_SEC", "1800") or 1800)
        except ValueError:
            raw = 1800.0
        return max(30.0, raw)

    @staticmethod
    def _claim_max_retries() -> int:
        try:
            raw = int(os.getenv("DISPATCH_CLAIM_MAX_RETRIES", "3") or 3)
        except ValueError:
            raw = 3
        return max(1, raw)

    @staticmethod
    def _parse_iso_ts(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    def _is_stale_running_task(
        self,
        task: TaskEnvelope,
        *,
        now: datetime,
        stale_after_sec: float,
    ) -> bool:
        if task.status != "running":
            return False
        last_touch = self._parse_iso_ts(task.updated_at) or self._parse_iso_ts(
            task.started_at
        )
        if last_touch is None:
            return False
        if last_touch.tzinfo is None:
            last_touch = last_touch.replace(tzinfo=now.tzinfo)
        age_sec = (now - last_touch).total_seconds()
        return age_sec >= stale_after_sec

    async def _mutate_tasks_atomically(
        self,
        *,
        mutate: Callable[[List[Dict[str, Any]]], Tuple[Any, bool]],
    ) -> Any:
        """Run task-table mutation under a single process/file lock."""
        async with self.tasks._inproc_lock:
            async with FileLock(self.tasks.lock_path):
                rows = self.tasks._read_all_unlocked()
                value, changed = mutate(rows)
                if changed:
                    self.tasks._write_all_unlocked(rows)
                return value

    async def submit_task(
        self,
        *,
        worker_id: str,
        instruction: str,
        source: str,
        backend: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> TaskEnvelope:
        task = TaskEnvelope(
            task_id=new_task_id("tsk"),
            worker_id=str(worker_id or "worker-main").strip() or "worker-main",
            instruction=str(instruction or "").strip(),
            source=str(source or "manager_dispatch").strip() or "manager_dispatch",
            backend=str(backend or "").strip(),
            status="pending",
            metadata=dict(metadata or {}),
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        await self.tasks.append(task.to_dict())
        return task

    async def claim_next(
        self,
        *,
        worker_id: str,
        claimer: str,
    ) -> TaskEnvelope | None:
        safe_worker_id = str(worker_id or "worker-main").strip() or "worker-main"
        safe_claimer = str(claimer or "worker").strip() or "worker"

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[TaskEnvelope | None, bool]:
            now = datetime.now().astimezone()
            now_text = now.isoformat(timespec="seconds")
            stale_after_sec = self._claim_stale_after_sec()
            max_retries = self._claim_max_retries()
            changed = False

            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.worker_id != safe_worker_id:
                    continue
                if not self._is_stale_running_task(
                    task,
                    now=now,
                    stale_after_sec=stale_after_sec,
                ):
                    continue

                task.retry_count = max(0, int(task.retry_count or 0)) + 1
                metadata = dict(task.metadata or {})
                recovery = metadata.get("_claim_recovery")
                recovery_obj = dict(recovery) if isinstance(recovery, dict) else {}
                recovery_obj.update(
                    {
                        "updated_at": now_text,
                        "last_claimed_by": str(task.claimed_by or ""),
                        "attempts": task.retry_count,
                    }
                )

                if task.retry_count >= max_retries:
                    task.status = "failed"
                    task.error = "worker_claim_stale_timeout"
                    task.ended_at = now_text
                    recovery_obj["state"] = "stale_claim_failed"
                    recovery_obj["reason"] = task.error
                else:
                    task.status = "pending"
                    task.claimed_by = ""
                    task.started_at = ""
                    task.error = ""
                    task.ended_at = ""
                    recovery_obj["state"] = "stale_claim_recovered"

                metadata["_claim_recovery"] = recovery_obj
                task.metadata = metadata
                task.updated_at = now_text
                rows[idx] = task.to_dict()
                changed = True

            chosen_idx = -1
            chosen_task: TaskEnvelope | None = None
            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.worker_id != safe_worker_id:
                    continue
                if task.status != "pending":
                    continue
                chosen_idx = idx
                chosen_task = task
                break

            if chosen_idx < 0 or chosen_task is None:
                return None, changed

            chosen_task.status = "running"
            chosen_task.claimed_by = safe_claimer
            chosen_task.started_at = now_iso()
            chosen_task.updated_at = chosen_task.started_at
            rows[chosen_idx] = chosen_task.to_dict()
            return chosen_task, True

        return await self._mutate_tasks_atomically(mutate=_mutate)

    async def finish_task(
        self, *, task_id: str, result: TaskResult
    ) -> TaskEnvelope | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        result_snapshot = result.to_dict()

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[TaskEnvelope | None, bool]:
            updated_task: TaskEnvelope | None = None
            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.task_id != safe_task_id:
                    continue
                metadata = dict(task.metadata or {})
                metadata["_latest_result"] = dict(result_snapshot)
                metadata.pop("_result_persist_error", None)
                task.metadata = metadata
                if task.status == "cancelled":
                    task.updated_at = now_iso()
                    if not str(task.ended_at or "").strip():
                        task.ended_at = task.updated_at
                    if not str(task.error or "").strip():
                        task.error = str(result.error or "").strip()
                    updated_task = task
                    rows[idx] = task.to_dict()
                    return updated_task, True
                task.status = "done" if bool(result.ok) else "failed"
                task.error = str(result.error or "").strip()
                task.ended_at = now_iso()
                task.updated_at = task.ended_at
                updated_task = task
                rows[idx] = task.to_dict()
                return updated_task, True
            return None, False

        updated_task = await self._mutate_tasks_atomically(mutate=_mutate)
        if updated_task is None:
            return None

        try:
            await self.results.append(result_snapshot)
        except Exception as exc:
            logger.error(
                "Dispatch queue result append failed task=%s err=%s",
                safe_task_id,
                exc,
                exc_info=True,
            )
            await self._mark_result_persist_error(
                task_id=safe_task_id,
                error=str(exc),
            )
        return updated_task

    async def _mark_result_persist_error(self, *, task_id: str, error: str) -> bool:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return False
        safe_error = str(error or "").strip()[:200]
        if not safe_error:
            return False

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[bool, bool]:
            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.task_id != safe_task_id:
                    continue
                metadata = dict(task.metadata or {})
                metadata["_result_persist_error"] = {
                    "error": safe_error,
                    "updated_at": now_iso(),
                }
                task.metadata = metadata
                task.updated_at = now_iso()
                rows[idx] = task.to_dict()
                return True, True
            return False, False

        return bool(await self._mutate_tasks_atomically(mutate=_mutate))

    async def bump_relay_retry(
        self,
        *,
        task_id: str,
        reason: str,
        retry_after_sec: float,
        max_retries: int,
    ) -> Dict[str, Any] | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        safe_reason = str(reason or "delivery_failed").strip() or "delivery_failed"
        safe_retry_after = max(0.0, float(retry_after_sec))
        safe_max_retries = max(1, int(max_retries or 1))

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any] | None, bool]:
            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.task_id != safe_task_id:
                    continue

                metadata = dict(task.metadata or {})
                relay = metadata.get("_relay")
                relay_obj = dict(relay) if isinstance(relay, dict) else {}

                attempts = max(0, int(relay_obj.get("attempts") or 0)) + 1
                now = datetime.now().astimezone()
                state = "dead_letter" if attempts >= safe_max_retries else "retrying"
                next_retry_at = ""
                dead_letter_at = ""
                if state == "retrying":
                    next_retry_at = (
                        now + timedelta(seconds=safe_retry_after)
                    ).isoformat(timespec="seconds")
                else:
                    dead_letter_at = now.isoformat(timespec="seconds")

                relay_obj.update(
                    {
                        "attempts": attempts,
                        "state": state,
                        "last_error": safe_reason,
                        "next_retry_at": next_retry_at,
                        "dead_letter_at": dead_letter_at,
                        "updated_at": now.isoformat(timespec="seconds"),
                    }
                )
                metadata["_relay"] = relay_obj
                task.metadata = metadata
                task.updated_at = now.isoformat(timespec="seconds")
                rows[idx] = task.to_dict()
                return dict(relay_obj), True
            return None, False

        return await self._mutate_tasks_atomically(mutate=_mutate)

    async def clear_relay_retry(self, task_id: str) -> bool:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return False

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[bool, bool]:
            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.task_id != safe_task_id:
                    continue
                metadata = dict(task.metadata or {})
                if "_relay" not in metadata:
                    return True, False
                metadata.pop("_relay", None)
                task.metadata = metadata
                task.updated_at = now_iso()
                rows[idx] = task.to_dict()
                return True, True
            return False, False

        return bool(await self._mutate_tasks_atomically(mutate=_mutate))

    async def requeue_dead_letter(
        self,
        *,
        task_id: str,
        reason: str = "manual_requeue",
    ) -> Dict[str, Any]:
        safe_task_id = str(task_id or "").strip()
        safe_reason = str(reason or "manual_requeue").strip() or "manual_requeue"
        if not safe_task_id:
            return {
                "ok": False,
                "task_id": "",
                "retried": False,
                "error": "missing_task_id",
                "summary": "task_id is required",
            }

        now_text = now_iso()

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.task_id != safe_task_id:
                    continue

                metadata = dict(task.metadata or {})
                relay = metadata.get("_relay")
                relay_obj = dict(relay) if isinstance(relay, dict) else {}
                relay_state = str(relay_obj.get("state") or "").strip().lower()
                if relay_state != "dead_letter":
                    return {
                        "ok": False,
                        "task_id": task.task_id,
                        "retried": False,
                        "error": "not_dead_letter",
                        "state": relay_state or "none",
                        "summary": "task is not in dead_letter state",
                    }, False

                history = metadata.get("_relay_requeue_history")
                history_rows = list(history) if isinstance(history, list) else []
                history_rows.append(
                    {
                        "at": now_text,
                        "reason": safe_reason,
                        "previous_attempts": max(
                            0, int(relay_obj.get("attempts") or 0)
                        ),
                        "last_error": str(relay_obj.get("last_error") or ""),
                    }
                )
                if len(history_rows) > 20:
                    history_rows = history_rows[-20:]

                metadata["_relay_requeue_history"] = history_rows
                metadata.pop("_relay", None)
                task.metadata = metadata
                task.delivered_at = ""
                task.updated_at = now_text
                rows[idx] = task.to_dict()
                return {
                    "ok": True,
                    "task_id": task.task_id,
                    "worker_id": task.worker_id,
                    "retried": True,
                    "summary": "dead-letter task requeued for relay",
                }, True

            return {
                "ok": False,
                "task_id": safe_task_id,
                "retried": False,
                "error": "task_not_found",
                "summary": "task not found",
            }, False

        return await self._mutate_tasks_atomically(mutate=_mutate)

    async def get_task(self, task_id: str) -> TaskEnvelope | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        rows = await self.tasks.read_all()
        for row in rows:
            task = TaskEnvelope.from_dict(row)
            if task.task_id == safe_task_id:
                return task
        return None

    async def update_progress(self, task_id: str, progress: Dict[str, Any]) -> bool:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return False
        async with self.tasks._inproc_lock:
            async with FileLock(self.tasks.lock_path):
                rows = self.tasks._read_all_unlocked()
                updated = False
                for row in rows:
                    if str(row.get("task_id") or "") != safe_task_id:
                        continue
                    meta = dict(row.get("metadata") or {})
                    meta["progress"] = progress
                    row["metadata"] = meta
                    row["updated_at"] = now_iso()
                    updated = True
                    break
                if updated:
                    self.tasks._write_all_unlocked(rows)
                return updated

    async def list_tasks(
        self,
        *,
        worker_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[TaskEnvelope]:
        safe_worker_id = str(worker_id or "").strip()
        safe_status = str(status or "").strip().lower()
        safe_limit = max(1, min(200, int(limit or 50)))
        rows = await self.tasks.read_all()
        matched: List[TaskEnvelope] = []
        for row in rows:
            task = TaskEnvelope.from_dict(row)
            if safe_worker_id and task.worker_id != safe_worker_id:
                continue
            if safe_status and task.status != safe_status:
                continue
            matched.append(task)
        matched.sort(key=lambda item: item.created_at, reverse=True)
        return matched[:safe_limit]

    async def list_running(self, *, limit: int = 20) -> List[TaskEnvelope]:
        return await self.list_tasks(status="running", limit=limit)

    async def list_undelivered(self, *, limit: int = 20) -> List[TaskEnvelope]:
        safe_limit = max(1, min(200, int(limit or 20)))
        rows = await self.tasks.read_all()
        matched: List[TaskEnvelope] = []
        for row in reversed(rows):
            task = TaskEnvelope.from_dict(row)
            if task.status not in {"done", "failed"}:
                continue
            if str(task.delivered_at or "").strip():
                continue
            matched.append(task)
            if len(matched) >= safe_limit:
                break
        return matched

    async def delivery_health(
        self,
        *,
        worker_id: str = "",
        dead_letter_limit: int = 20,
    ) -> Dict[str, Any]:
        safe_worker_id = str(worker_id or "").strip()
        safe_limit = max(1, min(200, int(dead_letter_limit or 20)))
        rows = await self.tasks.read_all()

        undelivered = 0
        retrying = 0
        dead_letter = 0
        result_persist_error = 0
        dead_letter_rows: List[Dict[str, Any]] = []

        for row in rows:
            task = TaskEnvelope.from_dict(row)
            if safe_worker_id and task.worker_id != safe_worker_id:
                continue

            if (
                task.status in {"done", "failed"}
                and not str(task.delivered_at or "").strip()
            ):
                undelivered += 1

            metadata = dict(task.metadata or {})
            relay = metadata.get("_relay")
            relay_obj = dict(relay) if isinstance(relay, dict) else {}
            state = str(relay_obj.get("state") or "").strip().lower()

            if state == "retrying":
                retrying += 1
            elif state == "dead_letter":
                dead_letter += 1
                dead_letter_rows.append(
                    {
                        "task_id": task.task_id,
                        "worker_id": task.worker_id,
                        "status": task.status,
                        "source": task.source,
                        "updated_at": task.updated_at,
                        "attempts": max(0, int(relay_obj.get("attempts") or 0)),
                        "last_error": str(relay_obj.get("last_error") or ""),
                        "next_retry_at": str(relay_obj.get("next_retry_at") or ""),
                        "dead_letter_at": str(relay_obj.get("dead_letter_at") or ""),
                    }
                )

            persist_error_obj = metadata.get("_result_persist_error")
            if isinstance(persist_error_obj, dict):
                result_persist_error += 1

        def _sort_key(item: Dict[str, Any]) -> float:
            ts = self._parse_iso_ts(str(item.get("dead_letter_at") or ""))
            if ts is None:
                ts = self._parse_iso_ts(str(item.get("updated_at") or ""))
            return ts.timestamp() if ts is not None else 0.0

        dead_letter_rows.sort(key=_sort_key, reverse=True)
        return {
            "worker_id": safe_worker_id,
            "undelivered": undelivered,
            "retrying": retrying,
            "dead_letter": dead_letter,
            "result_persist_error": result_persist_error,
            "recent_dead_letters": dead_letter_rows[:safe_limit],
        }

    async def mark_delivered(self, task_id: str) -> bool:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return False

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[bool, bool]:
            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                if task.task_id != safe_task_id:
                    continue
                task.delivered_at = now_iso()
                task.updated_at = task.delivered_at
                rows[idx] = task.to_dict()
                return True, True
            return False, False

        return bool(await self._mutate_tasks_atomically(mutate=_mutate))

    async def latest_result(self, task_id: str) -> TaskResult | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        rows = await self.results.read_all()
        for row in reversed(rows):
            result = TaskResult.from_dict(row)
            if result.task_id == safe_task_id:
                return result
        task = await self.get_task(safe_task_id)
        if task is None:
            return None
        metadata = dict(task.metadata or {})
        snapshot = metadata.get("_latest_result")
        if not isinstance(snapshot, dict):
            return None
        snapshot_obj = dict(snapshot)
        if not str(snapshot_obj.get("task_id") or "").strip():
            snapshot_obj["task_id"] = safe_task_id
        if not str(snapshot_obj.get("worker_id") or "").strip():
            snapshot_obj["worker_id"] = str(task.worker_id or "")
        return TaskResult.from_dict(snapshot_obj)

    async def cancel_for_user(
        self,
        *,
        user_id: str,
        reason: str,
        include_running: bool,
    ) -> Dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        safe_reason = str(reason or "cancelled_by_user").strip() or "cancelled_by_user"

        def _mutate(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
            pending_cancelled = 0
            running_signaled = 0
            job_ids: List[str] = []
            changed = False

            for idx, row in enumerate(rows):
                task = TaskEnvelope.from_dict(row)
                metadata_user = str(task.metadata.get("user_id") or "").strip()
                if not safe_user_id or metadata_user != safe_user_id:
                    continue
                if task.status == "pending":
                    task.status = "cancelled"
                    task.error = safe_reason
                    task.ended_at = now_iso()
                    task.updated_at = task.ended_at
                    rows[idx] = task.to_dict()
                    pending_cancelled += 1
                    job_ids.append(task.task_id)
                    changed = True
                    continue
                if include_running and task.status == "running":
                    task.status = "cancelled"
                    task.error = safe_reason
                    task.ended_at = now_iso()
                    task.updated_at = task.ended_at
                    rows[idx] = task.to_dict()
                    running_signaled += 1
                    job_ids.append(task.task_id)
                    changed = True

            return {
                "pending_cancelled": pending_cancelled,
                "running_signaled": running_signaled,
                "job_ids": job_ids,
            }, changed

        return await self._mutate_tasks_atomically(mutate=_mutate)


dispatch_queue = DispatchQueue()
