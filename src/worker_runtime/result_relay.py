from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, Dict

from core.heartbeat_store import heartbeat_store
from core.platform.registry import adapter_manager
from worker_runtime.task_file_store import worker_task_file_store

logger = logging.getLogger(__name__)


def _split_chunks(text: str, limit: int = 3500) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if len(raw) <= limit:
        return [raw]

    chunks: list[str] = []
    rest = raw
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n\n", 0, limit)
        if cut < int(limit * 0.6):
            cut = rest.rfind("\n", 0, limit)
        if cut < int(limit * 0.4):
            cut = limit
        part = rest[:cut].strip()
        if part:
            chunks.append(part)
        rest = rest[cut:].strip()
    return chunks


def _extract_payload(
    result: Dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    payload = result.get("payload") if isinstance(result, dict) else {}
    payload_obj: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            payload_obj[str(key)] = value
    ui_source = payload_obj.get("ui")
    ui: dict[str, Any] = {}
    if isinstance(ui_source, dict):
        for key, value in ui_source.items():
            ui[str(key)] = value

    text = ""
    for key in (
        "text",
        "result",
        "summary",
        "message",
    ):
        text = str(payload_obj.get(key) or result.get(key) or "").strip()
        if text:
            break

    if text and "text" not in payload_obj:
        payload_obj["text"] = text
    if ui and "ui" not in payload_obj:
        payload_obj["ui"] = ui
    return text, ui, payload_obj


def _build_delivery_text(job: Dict[str, Any]) -> tuple[str, dict[str, Any]]:
    result = dict(job.get("result") or {})
    metadata = job.get("metadata")
    meta = dict(metadata) if isinstance(metadata, dict) else {}
    ok = bool(result.get("ok"))
    worker_name = str(
        result.get("worker_name")
        or meta.get("worker_name")
        or job.get("worker_id")
        or "执行助手"
    )
    text, ui, _payload = _extract_payload(result)

    if ok:
        body = text or str(result.get("summary") or "任务执行完成。")
        final_text = f"✅ {worker_name} 已完成任务\n\n{body}".strip()
    else:
        error = str(result.get("error") or job.get("error") or "未知错误").strip()
        summary = str(result.get("summary") or "").strip()
        detail = summary or text or error
        final_text = f"❌ {worker_name} 任务执行失败\n\n{detail}".strip()
    return final_text, ui


class WorkerResultRelay:
    """Deliver finished worker job results back to user chats."""

    def __init__(self) -> None:
        self.enabled = (
            os.getenv("WORKER_RESULT_RELAY_ENABLED", "true").strip().lower() == "true"
        )
        self.tick_sec = max(1.0, float(os.getenv("WORKER_RESULT_RELAY_TICK_SEC", "2")))
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Worker result relay disabled by env.")
            return
        if self._loop_task and not self._loop_task.done():
            return
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="worker-result-relay"
        )
        logger.info("Worker result relay started. tick=%.1fs", self.tick_sec)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._loop_task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Worker result relay tick error: %s", exc, exc_info=True)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.tick_sec)
            except asyncio.TimeoutError:
                continue

    async def process_once(self) -> None:
        rows = await worker_task_file_store.list_undelivered(limit=20)
        for job in rows:
            job_id = str(job.get("job_id") or "").strip()
            if not job_id:
                continue

            metadata = job.get("metadata")
            meta = dict(metadata) if isinstance(metadata, dict) else {}
            platform, chat_id = await self._resolve_delivery_target(meta)

            if not platform or not chat_id:
                await worker_task_file_store.mark_delivered(
                    job_id, detail="no_delivery_target"
                )
                continue

            delivered = await self._deliver_job(
                platform=platform, chat_id=chat_id, job=job
            )
            if delivered:
                await worker_task_file_store.mark_delivered(job_id, detail="delivered")

    async def _resolve_delivery_target(
        self,
        meta: Dict[str, Any],
    ) -> tuple[str, str]:
        platform = str(meta.get("platform") or "").strip().lower()
        chat_id = str(meta.get("chat_id") or "").strip()
        if platform and platform != "heartbeat_daemon" and chat_id:
            return platform, chat_id

        user_id = str(meta.get("user_id") or "").strip()
        if not user_id:
            return "", ""
        target = await heartbeat_store.get_delivery_target(user_id)
        target_platform = str(target.get("platform") or "").strip().lower()
        target_chat_id = str(target.get("chat_id") or "").strip()
        if target_platform and target_chat_id:
            return target_platform, target_chat_id
        return "", ""

    async def _deliver_job(
        self, *, platform: str, chat_id: str, job: Dict[str, Any]
    ) -> bool:
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            logger.warning(
                "Worker relay skip: adapter missing platform=%s job=%s",
                platform,
                job.get("job_id"),
            )
            return False

        text, ui = _build_delivery_text(job)
        if not text:
            text = "任务执行完成，但无可展示输出。"

        chunks = _split_chunks(text)
        if not chunks:
            return True

        try:
            total = len(chunks)
            for idx, chunk in enumerate(chunks, start=1):
                payload = chunk
                if total > 1:
                    payload = f"[{idx}/{total}]\n{chunk}"

                send = getattr(adapter, "send_message", None)
                if callable(send):
                    kwargs: Dict[str, Any] = {"chat_id": chat_id, "text": payload}
                    if idx == 1 and ui and total == 1:
                        kwargs["ui"] = ui
                    result = send(**kwargs)
                    if inspect.isawaitable(result):
                        await result
                    continue
                return False
            return True
        except Exception as exc:
            logger.error(
                "Worker relay delivery failed job=%s platform=%s chat=%s err=%s",
                job.get("job_id"),
                platform,
                chat_id,
                exc,
            )
            return False


worker_result_relay = WorkerResultRelay()
