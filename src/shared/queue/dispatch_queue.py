from __future__ import annotations

import os
from typing import Any, Dict, List

from shared.contracts.dispatch import TaskEnvelope, TaskResult, new_task_id, now_iso
from shared.queue.jsonl_queue import JsonlTable


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
        rows = await self.tasks.read_all()
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
            return None

        chosen_task.status = "running"
        chosen_task.claimed_by = safe_claimer
        chosen_task.started_at = now_iso()
        chosen_task.updated_at = chosen_task.started_at
        rows[chosen_idx] = chosen_task.to_dict()
        await self.tasks.write_all(rows)
        return chosen_task

    async def finish_task(
        self, *, task_id: str, result: TaskResult
    ) -> TaskEnvelope | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        rows = await self.tasks.read_all()
        updated_task: TaskEnvelope | None = None
        for idx, row in enumerate(rows):
            task = TaskEnvelope.from_dict(row)
            if task.task_id != safe_task_id:
                continue
            task.status = "done" if bool(result.ok) else "failed"
            task.error = str(result.error or "").strip()
            task.ended_at = now_iso()
            task.updated_at = task.ended_at
            updated_task = task
            rows[idx] = task.to_dict()
            break

        if updated_task is None:
            return None

        await self.tasks.write_all(rows)
        await self.results.append(result.to_dict())
        return updated_task

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
        rows = await self.list_tasks(limit=safe_limit * 2)
        matched = [
            row
            for row in rows
            if row.status in {"done", "failed"}
            and not str(row.delivered_at or "").strip()
        ]
        return matched[:safe_limit]

    async def mark_delivered(self, task_id: str) -> bool:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return False
        rows = await self.tasks.read_all()
        updated = False
        for idx, row in enumerate(rows):
            task = TaskEnvelope.from_dict(row)
            if task.task_id != safe_task_id:
                continue
            task.delivered_at = now_iso()
            task.updated_at = task.delivered_at
            rows[idx] = task.to_dict()
            updated = True
            break
        if not updated:
            return False
        await self.tasks.write_all(rows)
        return True

    async def latest_result(self, task_id: str) -> TaskResult | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        rows = await self.results.read_all()
        for row in reversed(rows):
            result = TaskResult.from_dict(row)
            if result.task_id == safe_task_id:
                return result
        return None

    async def cancel_for_user(
        self,
        *,
        user_id: str,
        reason: str,
        include_running: bool,
    ) -> Dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        safe_reason = str(reason or "cancelled_by_user").strip() or "cancelled_by_user"
        rows = await self.tasks.read_all()
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

        if changed:
            await self.tasks.write_all(rows)

        return {
            "pending_cancelled": pending_cancelled,
            "running_signaled": running_signaled,
            "job_ids": job_ids,
        }


dispatch_queue = DispatchQueue()
