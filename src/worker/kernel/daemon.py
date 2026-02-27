from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any, Set

from shared.contracts.dispatch import TaskEnvelope, TaskResult
from shared.queue.dispatch_queue import dispatch_queue
from worker.kernel.program_loader import program_loader

logger = logging.getLogger(__name__)


class WorkerKernelDaemon:
    def __init__(self, *, queue=None, loader=None) -> None:
        self.poll_sec = max(0.5, float(os.getenv("WORKER_KERNEL_POLL_SEC", "1.0")))
        self.cancel_poll_sec = max(
            0.05,
            float(os.getenv("WORKER_TASK_CANCEL_POLL_SEC", "0.5")),
        )
        self.max_concurrency = max(
            1,
            int(os.getenv("WORKER_KERNEL_MAX_CONCURRENCY", "2")),
        )
        self.queue = queue if queue is not None else dispatch_queue
        self.loader = loader if loader is not None else program_loader
        self.worker_id = str(
            os.getenv("WORKER_KERNEL_ID", "worker-main").strip() or "worker-main"
        )
        self.worker_identity = str(
            os.getenv("WORKER_KERNEL_IDENTITY", "x-bot-worker").strip()
            or "x-bot-worker"
        )
        self.default_program_id = str(
            os.getenv("WORKER_DEFAULT_PROGRAM_ID", "default-worker").strip()
            or "default-worker"
        )
        self.default_program_version = str(
            os.getenv("WORKER_DEFAULT_PROGRAM_VERSION", "v1").strip() or "v1"
        )
        self._stop_event = asyncio.Event()
        self._running: Set[asyncio.Task] = set()

    async def start(self) -> None:
        self.loader.ensure_program_artifact(
            program_id=self.default_program_id,
            version=self.default_program_version,
        )
        logger.info(
            "Worker kernel started. worker_id=%s poll=%.1fs max=%s",
            self.worker_id,
            self.poll_sec,
            self.max_concurrency,
        )
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Worker kernel tick failed: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_sec)
            except asyncio.TimeoutError:
                continue

        for task in list(self._running):
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)

    async def stop(self) -> None:
        self._stop_event.set()

    async def _tick(self) -> None:
        self._running = {task for task in self._running if not task.done()}
        while len(self._running) < self.max_concurrency:
            task = await self.queue.claim_next(
                worker_id=self.worker_id,
                claimer=self.worker_identity,
            )
            if task is None:
                return
            logger.info(
                "Worker kernel claimed task id=%s worker=%s backend=%s source=%s",
                task.task_id,
                task.worker_id,
                task.backend,
                task.source,
            )
            running = asyncio.create_task(
                self._run_task(task),
                name=f"worker-kernel-{task.task_id}",
            )
            self._running.add(running)

    async def _wait_for_task_cancel(self, task_id: str) -> TaskEnvelope | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        while not self._stop_event.is_set():
            current = await self.queue.get_task(safe_task_id)
            if current is None:
                return None
            if current.status == "cancelled":
                return current
            await asyncio.sleep(self.cancel_poll_sec)
        return None

    def _annotate_result(
        self,
        *,
        task: TaskEnvelope,
        result: TaskResult,
        program_id: str,
        version: str,
    ) -> TaskResult:
        payload = dict(result.payload or {})
        text = str(payload.get("text") or "").strip()
        if not text:
            text = str(result.summary or result.error or "").strip()
            if text:
                payload["text"] = text

        payload.setdefault("_result_writer", "worker_kernel")
        payload.setdefault("_execution_path", "worker.kernel.daemon")
        payload.setdefault("_claimed_by", str(task.claimed_by or self.worker_identity))
        payload.setdefault("_program_id", str(program_id or ""))
        payload.setdefault("_program_version", str(version or ""))
        payload.setdefault("_task_source", str(task.source or ""))
        payload.setdefault("_task_backend", str(task.backend or ""))

        result.task_id = str(result.task_id or task.task_id)
        result.worker_id = str(result.worker_id or self.worker_id)
        result.payload = payload
        if not str(result.summary or "").strip():
            result.summary = str(text or "worker task completed")[:200]
        return result

    async def _run_task(self, task: TaskEnvelope) -> None:
        program_id = str(task.metadata.get("program_id") or self.default_program_id)
        version = str(
            task.metadata.get("program_version") or self.default_program_version
        )
        cancel_watch_task: asyncio.Task | None = None
        run_program_task: asyncio.Task | None = None
        try:
            program = self.loader.load_program(program_id=program_id, version=version)
            run_program_task = asyncio.create_task(
                program.run(
                    task,
                    {
                        "worker_id": self.worker_id,
                        "program_id": program_id,
                        "program_version": version,
                    },
                ),
                name=f"worker-program-{task.task_id}",
            )
            cancel_watch_task = asyncio.create_task(
                self._wait_for_task_cancel(task.task_id),
                name=f"worker-cancel-watch-{task.task_id}",
            )
            done, _pending = await asyncio.wait(
                {run_program_task, cancel_watch_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_watch_task in done:
                try:
                    cancelled_task = cancel_watch_task.result()
                except Exception as exc:
                    logger.warning(
                        "Worker kernel cancel watcher failed task_id=%s err=%s",
                        task.task_id,
                        exc,
                    )
                    cancelled_task = None
                if cancelled_task is not None and not run_program_task.done():
                    run_program_task.cancel()
                    try:
                        await run_program_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.debug(
                            "Worker kernel ignored program exception after cancel "
                            "task_id=%s err=%s",
                            task.task_id,
                            exc,
                        )

                    reason = (
                        str(cancelled_task.error or "").strip() or "cancelled_by_user"
                    )
                    message = "worker task cancelled"
                    result = TaskResult(
                        task_id=task.task_id,
                        worker_id=self.worker_id,
                        ok=False,
                        summary=message,
                        error=reason,
                        payload={
                            "text": message,
                            "cancelled": True,
                            "cancel_reason": reason,
                        },
                    )
                    result = self._annotate_result(
                        task=task,
                        result=result,
                        program_id=program_id,
                        version=version,
                    )
                    logger.info(
                        "Worker kernel cancelled running task id=%s reason=%s",
                        task.task_id,
                        reason,
                    )
                    await self.queue.finish_task(task_id=task.task_id, result=result)
                    return

            if cancel_watch_task and not cancel_watch_task.done():
                cancel_watch_task.cancel()
                try:
                    await cancel_watch_task
                except asyncio.CancelledError:
                    pass

            if run_program_task is None:
                raise RuntimeError("worker run task not created")
            try:
                if run_program_task.done():
                    result = run_program_task.result()
                else:
                    result = await run_program_task
            except asyncio.CancelledError:
                message = "worker task cancelled"
                result = TaskResult(
                    task_id=task.task_id,
                    worker_id=self.worker_id,
                    ok=False,
                    summary=message,
                    error=message,
                    payload={"text": message, "cancelled": True},
                )
            if not isinstance(result, TaskResult):
                result_payload = dict(result) if isinstance(result, dict) else {}
                result = TaskResult(
                    task_id=task.task_id,
                    worker_id=self.worker_id,
                    ok=bool(result_payload.get("ok")),
                    summary=str(result_payload.get("summary") or ""),
                    error=str(result_payload.get("error") or ""),
                    payload=result_payload,
                )
        except Exception as exc:
            message = str(exc)
            logger.error(
                "Worker kernel task failed task_id=%s err=%s",
                task.task_id,
                message,
                exc_info=True,
            )
            result = TaskResult(
                task_id=task.task_id,
                worker_id=self.worker_id,
                ok=False,
                summary=message,
                error=message,
                payload={"text": message},
            )
        finally:
            if cancel_watch_task and not cancel_watch_task.done():
                cancel_watch_task.cancel()
                try:
                    await cancel_watch_task
                except asyncio.CancelledError:
                    pass
        result = self._annotate_result(
            task=task,
            result=result,
            program_id=program_id,
            version=version,
        )
        logger.info(
            "Worker kernel finished task id=%s ok=%s writer=%s summary=%s",
            task.task_id,
            bool(result.ok),
            str((result.payload or {}).get("_result_writer") or ""),
            str(result.summary or "")[:160],
        )
        await self.queue.finish_task(task_id=task.task_id, result=result)


async def run_worker_kernel() -> None:
    daemon = WorkerKernelDaemon()
    loop = asyncio.get_running_loop()

    def _handle_stop(*_args) -> None:
        loop.create_task(daemon.stop())

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _handle_stop)
            except NotImplementedError:
                pass

    await daemon.start()
