from __future__ import annotations

import asyncio
import logging
import os

from manager.dispatch.web_accounting_auto_image import (
    run_web_accounting_auto_image_task,
)
from shared.contracts.dispatch import TaskEnvelope, TaskResult
from shared.queue.dispatch_queue import dispatch_queue

logger = logging.getLogger(__name__)


class ManagerDispatchExecutor:
    def __init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task | None = None

    @staticmethod
    def _worker_id() -> str:
        return (
            str(os.getenv("MANAGER_DISPATCH_WORKER_ID", "manager-main")).strip()
            or "manager-main"
        )

    @staticmethod
    def _poll_sec() -> float:
        try:
            raw = float(os.getenv("MANAGER_DISPATCH_POLL_SEC", "1.0"))
        except ValueError:
            raw = 1.0
        return max(0.2, raw)

    async def start(self) -> None:
        if self._runner_task and not self._runner_task.done():
            return
        self._stop_event = asyncio.Event()
        self._runner_task = asyncio.create_task(self._run_loop())
        logger.info(
            "Manager dispatch executor started. worker_id=%s poll=%.1fs",
            self._worker_id(),
            self._poll_sec(),
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._runner_task is None:
            return
        if not self._runner_task.done():
            self._runner_task.cancel()
        try:
            await self._runner_task
        except asyncio.CancelledError:
            pass
        finally:
            self._runner_task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            task: TaskEnvelope | None = None
            try:
                task = await dispatch_queue.claim_next(
                    worker_id=self._worker_id(),
                    claimer="manager-dispatch-executor",
                )
                if task is None:
                    await asyncio.sleep(self._poll_sec())
                    continue

                result = await self._execute_task(task)
                await dispatch_queue.finish_task(task_id=task.task_id, result=result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Manager dispatch executor loop failed: %s",
                    exc,
                    exc_info=True,
                )
                if task is not None:
                    fallback = TaskResult(
                        task_id=task.task_id,
                        worker_id=task.worker_id,
                        ok=False,
                        summary="manager dispatch executor failed",
                        error=str(exc)[:200],
                        payload={"text": str(exc)[:500]},
                    )
                    try:
                        await dispatch_queue.finish_task(
                            task_id=task.task_id, result=fallback
                        )
                    except Exception:
                        logger.exception("Failed to finish errored manager task")
                await asyncio.sleep(self._poll_sec())

    async def _execute_task(self, task: TaskEnvelope) -> TaskResult:
        mode = str((task.metadata or {}).get("execution_mode") or "").strip()
        if mode == "web_accounting_auto_image":
            try:
                return await run_web_accounting_auto_image_task(task)
            except Exception as exc:
                message = str(exc or "manager web accounting auto-image failed").strip()
                return TaskResult(
                    task_id=task.task_id,
                    worker_id=task.worker_id,
                    ok=False,
                    summary=message[:200],
                    error=message[:200],
                    payload={"text": message[:500]},
                )

        detail = f"unsupported manager execution mode: {mode or '<empty>'}"
        return TaskResult(
            task_id=task.task_id,
            worker_id=task.worker_id,
            ok=False,
            summary=detail[:200],
            error=detail[:200],
            payload={"text": detail[:500]},
        )


manager_dispatch_executor = ManagerDispatchExecutor()
