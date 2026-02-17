from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from core.task_inbox import task_inbox
from core.worker_runtime import worker_runtime
from core.worker_store import worker_registry, worker_task_store


def _score_worker(goal: str, worker: Dict[str, Any]) -> int:
    text = str(goal or "").lower()
    score = 0
    status = str(worker.get("status") or "").lower()
    if status == "ready":
        score += 100
    elif status == "busy":
        score -= 100

    backend = str(worker.get("backend") or "").lower()
    if backend == "core-agent":
        score += 5
    if backend in {"shell", "bash", "sh"} and any(
        token in text for token in ("命令", "shell", "bash", "脚本", "运行")
    ):
        score += 30

    capabilities = [str(item).lower() for item in (worker.get("capabilities") or [])]
    merged_cap = " ".join(capabilities)
    if any(token in text for token in ("rss", "订阅", "feed")) and (
        "rss" in merged_cap or "feed" in merged_cap
    ):
        score += 40
    if (
        any(token in text for token in ("股票", "stock", "quote"))
        and "stock" in merged_cap
    ):
        score += 40
    if any(token in text for token in ("部署", "deploy", "docker")) and any(
        token in merged_cap for token in ("deploy", "docker", "ops")
    ):
        score += 40
    if any(token in text for token in ("测试", "test", "代码", "refactor")) and any(
        token in merged_cap for token in ("code", "test", "dev")
    ):
        score += 20
    return score


@dataclass
class WorkerSelection:
    worker_id: str
    reason: str
    auto_selected: bool = False


class DispatchTools:
    """Manager-facing worker dispatch helpers."""

    async def list_workers(self) -> Dict[str, Any]:
        workers = await worker_registry.list_workers()
        rows: List[Dict[str, Any]] = []
        for item in workers:
            worker_id = str(item.get("id") or "")
            configured_backend = str(item.get("backend") or "core-agent")
            effective_backend, _detail = worker_runtime._select_allowed_backend(
                worker_id=worker_id or "worker-main",
                requested_backend=None,
                configured_backend=configured_backend,
            )
            rows.append(
                {
                    "id": worker_id,
                    "name": str(item.get("name") or ""),
                    "status": str(item.get("status") or "unknown"),
                    "backend": str(effective_backend or "none"),
                    "configured_backend": configured_backend,
                    "capabilities": list(item.get("capabilities") or []),
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
                    worker_id=str(worker.get("id") or preferred), reason=reason
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
            workers,
            key=lambda item: _score_worker(goal, item),
            reverse=True,
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
        selected = await self._choose_worker(
            goal=task_instruction,
            preferred_worker_id=worker_id,
        )
        selected_worker_id = selected.worker_id
        selected_worker = await worker_registry.get_worker(selected_worker_id)
        selected_worker_name = (
            str((selected_worker or {}).get("name") or selected_worker_id).strip()
            or selected_worker_id
        )

        inbox_task_id = str(meta.get("task_inbox_id") or "").strip()
        if inbox_task_id:
            await task_inbox.assign_worker(
                inbox_task_id,
                worker_id=selected_worker_id,
                reason=selected.reason,
            )

        result = await worker_runtime.execute_task(
            worker_id=selected_worker_id,
            source=str(source or "manager_dispatch"),
            instruction=task_instruction,
            backend=str(backend or "").strip() or None,
            metadata=meta,
        )

        worker_text = str(
            result.get("text")
            or result.get("result")
            or result.get("message")
            or result.get("summary")
            or ""
        )
        worker_ui = result.get("ui")
        if not isinstance(worker_ui, dict):
            worker_ui = {}
        worker_payload = result.get("payload")
        if not isinstance(worker_payload, dict):
            worker_payload = {"text": worker_text}
            if worker_ui:
                worker_payload["ui"] = worker_ui

        if inbox_task_id:
            if result.get("ok"):
                await task_inbox.update_status(
                    inbox_task_id,
                    "running",
                    event="worker_done",
                    detail=str(result.get("summary") or "")[:200],
                    result={
                        "worker_result": result,
                        "worker_id": selected_worker_id,
                        "worker_name": selected_worker_name,
                        "text": worker_text,
                        "ui": worker_ui,
                        "payload": worker_payload,
                    },
                )
            else:
                await task_inbox.fail(
                    inbox_task_id,
                    error=str(
                        result.get("error") or result.get("summary") or "worker_failed"
                    ),
                    result={
                        "worker_result": result,
                        "worker_id": selected_worker_id,
                        "worker_name": selected_worker_name,
                        "text": worker_text,
                        "ui": worker_ui,
                        "payload": worker_payload,
                    },
                )

        return {
            "ok": bool(result.get("ok")),
            "worker_id": selected_worker_id,
            "worker_name": selected_worker_name,
            "task_id": str(result.get("task_id") or ""),
            "backend": str(result.get("backend") or ""),
            "result": str(result.get("result") or ""),
            "summary": str(result.get("summary") or ""),
            "text": worker_text,
            "ui": worker_ui,
            "payload": worker_payload,
            "error": str(result.get("error") or ""),
            "auto_selected": bool(selected.auto_selected),
            "selection_reason": selected.reason,
            "runtime_mode": str(result.get("runtime_mode") or ""),
            "terminal": True,
            "task_outcome": "done" if result.get("ok") else "failed",
        }

    async def worker_status(
        self,
        *,
        worker_id: str = "",
        limit: int = 10,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(50, int(limit or 10)))
        tasks = await worker_task_store.list_recent(
            worker_id=str(worker_id or "").strip(),
            limit=safe_limit,
        )
        return {
            "ok": True,
            "worker_id": str(worker_id or "").strip(),
            "tasks": tasks,
            "summary": f"{len(tasks)} recent worker task(s)",
        }


dispatch_tools = DispatchTools()
