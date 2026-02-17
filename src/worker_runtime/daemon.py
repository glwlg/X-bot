from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any, Dict

from core.worker_runtime import worker_runtime
from worker_runtime.task_file_store import worker_task_file_store

logger = logging.getLogger(__name__)


class WorkerDaemon:
    def __init__(self) -> None:
        self.poll_sec = max(0.5, float(os.getenv("WORKER_DAEMON_POLL_SEC", "1.5")))
        self.max_concurrency = max(
            1, int(os.getenv("WORKER_DAEMON_MAX_CONCURRENCY", "2"))
        )
        self.worker_identity = (
            os.getenv("WORKER_DAEMON_ID", "x-bot-worker").strip() or "x-bot-worker"
        )
        self.worker_scope = os.getenv("WORKER_DAEMON_WORKER_ID", "").strip()
        self._stop_event = asyncio.Event()
        self._running: set[asyncio.Task] = set()
        self._idle_ticks = 0

    async def start(self) -> None:
        await worker_task_file_store.ensure_task_files(worker_id=self.worker_scope)
        logger.info(
            "Worker daemon started. id=%s scope=%s poll=%.1fs max_concurrency=%s runtime_mode=%s",
            self.worker_identity,
            self.worker_scope or "all",
            self.poll_sec,
            self.max_concurrency,
            worker_runtime.runtime_mode,
        )
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Worker daemon tick failed: %s", exc, exc_info=True)

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
            job = await worker_task_file_store.claim_next(
                claimer=self.worker_identity,
                worker_id=self.worker_scope,
            )
            if not job:
                self._idle_ticks += 1
                log_every = max(1, int(30 / self.poll_sec))
                if self._idle_ticks % log_every == 0:
                    logger.info(
                        "Worker daemon idle: no TASK.md jobs yet (scope=%s)",
                        self.worker_scope or "all",
                    )
                return
            self._idle_ticks = 0
            task = asyncio.create_task(
                self._run_job(job), name=f"worker-job-{job.get('job_id')}"
            )
            self._running.add(task)

    async def _run_job(self, job: Dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "").strip()
        worker_id = str(job.get("worker_id") or "worker-main").strip() or "worker-main"
        instruction = str(job.get("instruction") or "").strip()
        backend = str(job.get("backend") or "").strip() or None
        source = (
            str(job.get("source") or "manager_dispatch").strip() or "manager_dispatch"
        )
        metadata = job.get("metadata")
        metadata_obj = dict(metadata) if isinstance(metadata, dict) else {}
        if not job_id:
            return

        logger.info(
            "Worker daemon picked job=%s worker=%s backend=%s source=%s session=%s",
            job_id,
            worker_id,
            backend or "",
            source,
            str(job.get("session_id") or ""),
        )

        try:
            result = await worker_runtime.execute_task(
                worker_id=worker_id,
                source=source,
                instruction=instruction,
                backend=backend,
                metadata=metadata_obj,
            )
        except Exception as exc:
            msg = f"worker execution exception: {exc}"
            logger.error(
                "Worker daemon job failed job=%s err=%s", job_id, exc, exc_info=True
            )
            await worker_task_file_store.finish(
                job_id,
                ok=False,
                error=msg,
                result={
                    "ok": False,
                    "error": msg,
                    "summary": msg,
                    "text": msg,
                    "payload": {"text": msg},
                },
            )
            return

        ok = bool(result.get("ok"))
        error = str(result.get("error") or "")
        summary = str(result.get("summary") or "")
        logger.info(
            "Worker daemon finished job=%s ok=%s summary=%s",
            job_id,
            ok,
            summary[:160].replace("\n", " "),
        )
        await worker_task_file_store.finish(job_id, ok=ok, result=result, error=error)


async def _main() -> None:
    daemon = WorkerDaemon()
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


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    )
    asyncio.run(_main())
