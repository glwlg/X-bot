from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any, Dict, List

from core.worker_store import worker_registry, worker_task_store
from shared.queue.dispatch_queue import dispatch_queue

logger = logging.getLogger(__name__)


def _score_worker(goal: str, worker: Dict[str, Any]) -> int:
    text = str(goal or "").lower()
    score = 0
    status = str(worker.get("status") or "").lower()
    if status == "ready":
        score += 100
    elif status == "busy":
        score -= 100

    capabilities = [str(item).lower() for item in (worker.get("capabilities") or [])]
    summary = str(worker.get("summary") or "").lower()
    merged_cap = " ".join(capabilities + [summary])

    if any(token in text for token in ("rss", "订阅", "feed")) and (
        "rss" in merged_cap or "feed" in merged_cap
    ):
        score += 40
    if any(token in text for token in ("股票", "stock", "quote")) and (
        "stock" in merged_cap
    ):
        score += 40
    if any(token in text for token in ("部署", "deploy", "docker")) and any(
        token in merged_cap for token in ("deploy", "docker", "ops")
    ):
        score += 40
    return score


@dataclass
class WorkerSelection:
    worker_id: str
    reason: str
    auto_selected: bool = False


class ManagerDispatchService:
    async def list_workers(self) -> Dict[str, Any]:
        workers = await worker_registry.list_workers()
        rows: List[Dict[str, Any]] = []
        for item in workers:
            rows.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "status": str(item.get("status") or "unknown"),
                    "backend": str(item.get("backend") or ""),
                    "capabilities": list(item.get("capabilities") or []),
                    "summary": str(item.get("summary") or ""),
                    "last_task_id": str(item.get("last_task_id") or ""),
                    "last_error": str(item.get("last_error") or ""),
                }
            )
        return {
            "ok": True,
            "workers": rows,
            "summary": f"{len(rows)} worker(s) available",
        }

    async def _choose_worker(
        self,
        *,
        goal: str,
        preferred_worker_id: str = "",
    ) -> WorkerSelection:
        preferred = str(preferred_worker_id or "").strip().lower()
        if preferred:
            worker = await worker_registry.get_worker(preferred)
            if worker:
                status = str(worker.get("status") or "").lower()
                reason = (
                    "preferred_worker" if status == "ready" else "preferred_worker_busy"
                )
                return WorkerSelection(
                    worker_id=str(worker.get("id") or preferred),
                    reason=reason,
                )

        workers = await worker_registry.list_workers()
        if not workers:
            worker = await worker_registry.ensure_default_worker()
            return WorkerSelection(
                worker_id=str(worker.get("id") or "worker-main"),
                reason="created_default_worker",
                auto_selected=True,
            )

        ranked = sorted(
            workers, key=lambda item: _score_worker(goal, item), reverse=True
        )
        picked = ranked[0]
        return WorkerSelection(
            worker_id=str(picked.get("id") or "worker-main"),
            reason="llm_unspecified_auto_pick",
            auto_selected=True,
        )

    async def dispatch_worker(
        self,
        *,
        instruction: str,
        worker_id: str = "",
        backend: str = "",
        source: str = "manager_dispatch",
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        task_instruction = str(instruction or "").strip()
        if not task_instruction:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "instruction is required",
            }

        meta = dict(metadata or {})
        if not str(meta.get("program_id") or "").strip():
            meta["program_id"] = (
                str(os.getenv("WORKER_DEFAULT_PROGRAM_ID", "default-worker")).strip()
                or "default-worker"
            )
        if not str(meta.get("program_version") or "").strip():
            meta["program_version"] = (
                str(os.getenv("WORKER_DEFAULT_PROGRAM_VERSION", "v1")).strip() or "v1"
            )
        selected = await self._choose_worker(
            goal=task_instruction,
            preferred_worker_id=worker_id,
        )
        selected_worker_id = selected.worker_id
        selected_worker = await worker_registry.get_worker(selected_worker_id)
        selected_worker_obj = dict(selected_worker or {})
        selected_worker_name = (
            str(selected_worker_obj.get("name") or selected_worker_id).strip()
            or selected_worker_id
        )
        meta.setdefault("worker_name", selected_worker_name)
        meta.setdefault("dispatch_component", "manager_dispatch_service")

        queued = await dispatch_queue.submit_task(
            worker_id=selected_worker_id,
            instruction=task_instruction,
            source=str(source or "manager_dispatch"),
            backend=str(backend or ""),
            metadata=meta,
        )
        try:
            await worker_task_store.upsert_task(
                task_id=queued.task_id,
                worker_id=selected_worker_id,
                source=str(source or "manager_dispatch"),
                instruction=task_instruction,
                status="queued",
                metadata=meta,
                retry_count=int(getattr(queued, "retry_count", 0) or 0),
                created_at=str(getattr(queued, "created_at", "") or ""),
                result_summary="queued by manager dispatch",
            )
        except Exception as exc:
            logger.warning(
                "Failed to mirror queued task into WorkerTaskStore task_id=%s err=%s",
                str(getattr(queued, "task_id", "") or ""),
                exc,
            )

        manager_hint = (
            "worker dispatch accepted; "
            f"worker_name={selected_worker_name}; "
            f"task_id={queued.task_id}; "
            "status=running_async; "
            "reply user naturally in Chinese and mention the task id once."
        )
        return {
            "ok": True,
            "worker_id": selected_worker_id,
            "worker_name": selected_worker_name,
            "task_id": queued.task_id,
            "backend": str(backend or selected_worker_obj.get("backend") or ""),
            "result": "",
            "summary": f"worker job queued: {queued.task_id}"[:200],
            "text": manager_hint,
            "ui": {},
            "payload": {
                "text": manager_hint,
                "dispatch": "queued",
                "worker_name": selected_worker_name,
                "task_id": queued.task_id,
                "manager_reply_style": "natural",
            },
            "error": "",
            "auto_selected": bool(selected.auto_selected),
            "selection_reason": selected.reason,
            "runtime_mode": "async_queue",
            "terminal": False,
            "task_outcome": "partial",
            "async_dispatch": True,
        }


manager_dispatch_service = ManagerDispatchService()
