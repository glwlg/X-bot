from __future__ import annotations

from typing import Any, Dict

from manager.dispatch.service import manager_dispatch_service
from shared.queue.dispatch_queue import dispatch_queue


class DispatchTools:
    async def list_workers(self) -> Dict[str, Any]:
        return await manager_dispatch_service.list_workers()

    async def dispatch_worker(
        self,
        *,
        instruction: str,
        worker_id: str = "",
        backend: str = "",
        source: str = "manager_dispatch",
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return await manager_dispatch_service.dispatch_worker(
            instruction=instruction,
            worker_id=worker_id,
            backend=backend,
            source=source,
            metadata=metadata,
        )

    async def worker_status(
        self,
        *,
        worker_id: str = "",
        limit: int = 10,
    ) -> Dict[str, Any]:
        safe_worker_id = str(worker_id or "").strip()
        safe_limit = max(1, min(50, int(limit or 10)))
        tasks = await dispatch_queue.list_tasks(
            worker_id=safe_worker_id,
            limit=safe_limit,
        )
        delivery_health = await dispatch_queue.delivery_health(
            worker_id=safe_worker_id,
            dead_letter_limit=min(20, safe_limit),
        )
        rows = [item.to_dict() for item in tasks]
        dead_letter = int(delivery_health.get("dead_letter") or 0)
        retrying = int(delivery_health.get("retrying") or 0)
        return {
            "ok": True,
            "worker_id": safe_worker_id,
            "tasks": rows,
            "delivery_health": delivery_health,
            "summary": (
                f"{len(rows)} recent worker task(s); "
                f"dead_letter={dead_letter}; retrying={retrying}"
            ),
        }

    async def retry_dead_letter(
        self,
        *,
        task_id: str,
        reason: str = "manual_requeue",
    ) -> Dict[str, Any]:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return {
                "ok": False,
                "task_id": "",
                "retried": False,
                "summary": "task_id is required",
            }

        result = await dispatch_queue.requeue_dead_letter(
            task_id=safe_task_id,
            reason=reason,
        )
        payload = dict(result or {})
        payload.setdefault("task_id", safe_task_id)
        payload.setdefault("retried", False)
        if payload.get("ok") is None:
            payload["ok"] = bool(payload.get("retried"))
        if not str(payload.get("summary") or "").strip():
            payload["summary"] = (
                "dead-letter task requeued"
                if bool(payload.get("retried"))
                else "dead-letter requeue failed"
            )
        return payload


dispatch_tools = DispatchTools()
