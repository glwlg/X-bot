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
        tasks = await dispatch_queue.list_tasks(
            worker_id=str(worker_id or "").strip(),
            limit=max(1, min(50, int(limit or 10))),
        )
        rows = [item.to_dict() for item in tasks]
        return {
            "ok": True,
            "worker_id": str(worker_id or "").strip(),
            "tasks": rows,
            "summary": f"{len(rows)} recent worker task(s)",
        }


dispatch_tools = DispatchTools()
