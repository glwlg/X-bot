from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, cast

from core.worker_runtime import worker_runtime
from core.worker_store import worker_registry, worker_task_store
from worker_runtime.task_file_store import worker_task_file_store as worker_job_store


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
    summary = str(worker.get("summary") or "").lower()
    merged_cap = " ".join(capabilities + [summary])
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
    if any(
        token in text
        for token in ("画", "绘图", "图片", "图像", "海报", "插画", "image", "draw")
    ) and any(
        token in merged_cap
        for token in ("image", "media", "draw", "绘图", "图片", "图像")
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
        selected_worker_obj = dict(selected_worker or {})
        selected_worker_name = (
            str(selected_worker_obj.get("name") or selected_worker_id).strip()
            or selected_worker_id
        )

        dispatch_mode = (
            os.getenv("WORKER_DISPATCH_MODE", "async").strip().lower() or "async"
        )
        if dispatch_mode not in {"sync", "async"}:
            dispatch_mode = "async"

        if dispatch_mode == "async":
            job_metadata = cast(Dict[str, Any], dict(meta))
            job_metadata["worker_id"] = selected_worker_id
            job_metadata["worker_name"] = selected_worker_name
            job_metadata["selection_reason"] = selected.reason
            if "session_id" not in job_metadata:
                session_id = str(meta.get("session_id") or "").strip()
                if session_id:
                    job_metadata["session_id"] = session_id

            queued_job = await worker_job_store.submit(
                worker_id=selected_worker_id,
                instruction=task_instruction,
                source=str(source or "manager_dispatch"),
                backend=str(backend or "").strip(),
                metadata=job_metadata,
            )
            queued_job_id = str(queued_job.get("job_id") or "")

            ack_text = (
                f"已派发给 {selected_worker_name} 处理（任务 {queued_job_id}）。"
                "我先继续和你聊天，完成后会自动把结果发给你。"
            )
            return {
                "ok": True,
                "worker_id": selected_worker_id,
                "worker_name": selected_worker_name,
                "task_id": queued_job_id,
                "backend": str(backend or selected_worker_obj.get("backend") or ""),
                "result": "",
                "summary": ack_text[:200],
                "text": ack_text,
                "ui": {},
                "payload": {"text": ack_text},
                "error": "",
                "auto_selected": bool(selected.auto_selected),
                "selection_reason": selected.reason,
                "runtime_mode": "async_queue",
                "terminal": True,
                "task_outcome": "done",
                "async_dispatch": True,
            }

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
        worker_payload_raw = result.get("payload")
        worker_payload: Dict[str, Any] = {}
        if isinstance(worker_payload_raw, dict):
            worker_payload.update(dict(worker_payload_raw))
        if not worker_payload:
            worker_payload = {"text": worker_text}
            if worker_ui:
                worker_payload["ui"] = worker_ui

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
