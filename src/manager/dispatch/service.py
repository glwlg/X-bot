from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from typing import Any, Dict, List

from core.worker_store import worker_registry, worker_task_store
from shared.queue.dispatch_queue import dispatch_queue

logger = logging.getLogger(__name__)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return int(default)


def _dispatch_priority(*, source: str, metadata: Dict[str, Any]) -> int:
    if "priority" in metadata:
        return max(-100, min(100, _safe_int(metadata.get("priority"), 0)))
    normalized_source = str(source or "").strip().lower()
    if normalized_source == "user_cmd":
        return 80
    if normalized_source == "user_chat":
        return 60
    if normalized_source == "heartbeat":
        return 20
    if normalized_source == "system":
        return 10
    return 40


def _score_worker(
    goal: str,
    worker: Dict[str, Any],
    *,
    metrics: Dict[str, Any],
    recent_error_rate: float,
) -> int:
    text = str(goal or "").lower()
    score = 0
    status = str(worker.get("status") or "").lower()
    if status == "ready":
        score += 100
    elif status == "busy":
        score -= 40

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

    running = max(0, _safe_int(metrics.get("running"), 0))
    pending = max(0, _safe_int(metrics.get("pending"), 0))
    queue_depth = max(0, _safe_int(metrics.get("queue_depth"), pending + running))
    score -= running * 25
    score -= pending * 12
    score -= queue_depth * 4
    score -= int(max(0.0, float(recent_error_rate or 0.0)) * 80)
    return score


@dataclass
class WorkerSelection:
    worker_id: str
    reason: str
    auto_selected: bool = False
    score: int = 0
    metrics: Dict[str, Any] = field(default_factory=dict)


class ManagerDispatchService:
    async def _recent_error_rate(self, worker_id: str, *, limit: int = 12) -> float:
        rows = await worker_task_store.list_recent(worker_id=worker_id, limit=limit)
        if not rows:
            return 0.0
        failures = 0
        checked = 0
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status not in {"done", "failed", "cancelled"}:
                continue
            checked += 1
            if status in {"failed", "cancelled"}:
                failures += 1
        if checked <= 0:
            return 0.0
        return round(failures / checked, 3)

    async def _worker_metrics_snapshot(self, worker_id: str) -> Dict[str, Any]:
        metrics = await dispatch_queue.worker_metrics(worker_id=worker_id, limit=50)
        metrics["recent_error_rate"] = await self._recent_error_rate(worker_id)
        return metrics

    async def list_workers(self) -> Dict[str, Any]:
        workers = await worker_registry.list_workers()
        rows: List[Dict[str, Any]] = []
        for item in workers:
            worker_id = str(item.get("id") or "")
            metrics = await self._worker_metrics_snapshot(worker_id)
            rows.append(
                {
                    "id": worker_id,
                    "name": str(item.get("name") or ""),
                    "status": str(item.get("status") or "unknown"),
                    "backend": str(item.get("backend") or ""),
                    "capabilities": list(item.get("capabilities") or []),
                    "summary": str(item.get("summary") or ""),
                    "last_task_id": str(item.get("last_task_id") or ""),
                    "last_error": str(item.get("last_error") or ""),
                    "load": {
                        "queue_depth": int(metrics.get("queue_depth") or 0),
                        "pending": int(metrics.get("pending") or 0),
                        "running": int(metrics.get("running") or 0),
                    },
                    "recent_error_rate": float(metrics.get("recent_error_rate") or 0.0),
                    "avg_dispatch_latency_sec": float(
                        metrics.get("avg_dispatch_latency_sec") or 0.0
                    ),
                    "avg_completion_sec": float(
                        metrics.get("avg_completion_sec") or 0.0
                    ),
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
                metrics = await self._worker_metrics_snapshot(str(worker.get("id") or preferred))
                reason = (
                    "preferred_worker" if status == "ready" else "preferred_worker_busy"
                )
                return WorkerSelection(
                    worker_id=str(worker.get("id") or preferred),
                    reason=reason,
                    score=999 if status == "ready" else 500,
                    metrics=metrics,
                )

        workers = await worker_registry.list_workers()
        if not workers:
            worker = await worker_registry.ensure_default_worker()
            metrics = await self._worker_metrics_snapshot(str(worker.get("id") or "worker-main"))
            return WorkerSelection(
                worker_id=str(worker.get("id") or "worker-main"),
                reason="created_default_worker",
                auto_selected=True,
                score=0,
                metrics=metrics,
            )

        ranked: List[tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        for item in workers:
            worker_id = str(item.get("id") or "").strip()
            metrics = await self._worker_metrics_snapshot(worker_id)
            recent_error_rate = float(metrics.get("recent_error_rate") or 0.0)
            score = _score_worker(
                goal,
                item,
                metrics=metrics,
                recent_error_rate=recent_error_rate,
            )
            ranked.append((score, item, metrics))
        ranked.sort(key=lambda item: item[0], reverse=True)
        picked_score, picked, picked_metrics = ranked[0]
        return WorkerSelection(
            worker_id=str(picked.get("id") or "worker-main"),
            reason="llm_unspecified_auto_pick",
            auto_selected=True,
            score=int(picked_score),
            metrics=picked_metrics,
        )

    async def dispatch_worker(
        self,
        *,
        instruction: str,
        worker_id: str = "",
        backend: str = "",
        priority: Any = None,
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
        has_explicit_priority = False
        try:
            explicit_priority = int(priority) if priority not in {None, ""} else 0
            has_explicit_priority = bool(explicit_priority) or "priority" in meta
        except Exception:
            explicit_priority = 0
        resolved_priority = (
            explicit_priority
            if has_explicit_priority
            else _dispatch_priority(source=source, metadata=meta)
        )
        priority = max(-100, min(100, int(resolved_priority)))
        meta["priority"] = priority
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
        meta.setdefault("selection_reason", selected.reason)
        meta.setdefault("selection_score", int(selected.score or 0))
        meta.setdefault("worker_metrics", dict(selected.metrics or {}))

        queued = await dispatch_queue.submit_task(
            worker_id=selected_worker_id,
            instruction=task_instruction,
            source=str(source or "manager_dispatch"),
            backend=str(backend or ""),
            priority=priority,
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
                "priority": priority,
            },
            "error": "",
            "auto_selected": bool(selected.auto_selected),
            "selection_reason": selected.reason,
            "selection_score": int(selected.score or 0),
            "worker_metrics": dict(selected.metrics or {}),
            "priority": priority,
            "runtime_mode": "async_queue",
            "terminal": False,
            "task_outcome": "partial",
            "async_dispatch": True,
        }


manager_dispatch_service = ManagerDispatchService()
