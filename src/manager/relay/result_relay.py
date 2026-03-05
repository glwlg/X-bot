from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.heartbeat_store import heartbeat_store
from core.platform.registry import adapter_manager
from services.md_converter import adapt_md_file_for_platform
from shared.contracts.dispatch import TaskEnvelope
from shared.queue.dispatch_queue import dispatch_queue

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
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
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
    for key in ("text", "result", "summary", "message"):
        text = str(payload_obj.get(key) or result.get(key) or "").strip()
        if text:
            break

    file_rows: list[dict[str, str]] = []
    raw_files = payload_obj.get("files")
    if not isinstance(raw_files, list):
        raw_files = result.get("files")

    if isinstance(raw_files, list):
        seen_paths: set[str] = set()
        seen_names: set[tuple[str, str]] = set()
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            path_text = str(item.get("path") or "").strip()
            if not path_text:
                continue
            path_obj = Path(path_text).expanduser().resolve()
            if not path_obj.exists() or not path_obj.is_file():
                continue
            kind = str(item.get("kind") or "document").strip().lower() or "document"
            if kind not in {"photo", "video", "audio", "document"}:
                kind = "document"
            filename = (
                str(item.get("filename") or path_obj.name).strip() or path_obj.name
            )
            caption = str(item.get("caption") or "").strip()[:500]
            path_key = str(path_obj)
            name_key = (kind, filename)
            if path_key in seen_paths or name_key in seen_names:
                continue
            seen_paths.add(path_key)
            seen_names.add(name_key)
            file_rows.append(
                {
                    "kind": kind,
                    "path": path_key,
                    "filename": filename,
                    "caption": caption,
                }
            )

    return text, ui, file_rows


def _build_delivery_text(
    task: TaskEnvelope,
    result: Dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
    ok = bool(result.get("ok"))
    worker_name = str(task.metadata.get("worker_name") or task.worker_id or "执行助手")
    text, ui, files = _extract_payload(result)

    if ok:
        body = text or str(result.get("summary") or "任务执行完成。")
        final_text = f"✅ {worker_name} 已完成任务\n\n{body}".strip()
    else:
        error = str(result.get("error") or task.error or "未知错误").strip()
        summary = str(result.get("summary") or "").strip()
        detail = summary or text or error
        final_text = f"❌ {worker_name} 任务执行失败\n\n{detail}".strip()
    return final_text, ui, files


class WorkerResultRelay:
    def __init__(self) -> None:
        self.enabled = (
            os.getenv("WORKER_RESULT_RELAY_ENABLED", "true").strip().lower() == "true"
        )
        self.tick_sec = max(1.0, float(os.getenv("WORKER_RESULT_RELAY_TICK_SEC", "2")))
        self.max_retries = max(
            1,
            int(os.getenv("WORKER_RESULT_RELAY_MAX_RETRIES", "6") or 6),
        )
        self.retry_base_sec = max(
            0.0,
            float(
                os.getenv(
                    "WORKER_RESULT_RELAY_RETRY_BASE_SEC",
                    str(self.tick_sec),
                )
                or self.tick_sec
            ),
        )
        self.retry_max_sec = max(
            self.retry_base_sec,
            float(os.getenv("WORKER_RESULT_RELAY_RETRY_MAX_SEC", "300") or 300),
        )
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
            self._run_loop(),
            name="worker-result-relay",
        )
        logger.info(
            "Worker result relay started. tick=%.1fs max_retries=%s base=%.1fs max_backoff=%.1fs",
            self.tick_sec,
            self.max_retries,
            self.retry_base_sec,
            self.retry_max_sec,
        )

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
        rows = await dispatch_queue.list_undelivered(limit=20)
        for task in rows:
            if self._is_dead_letter(task):
                continue
            if self._is_backoff_pending(task):
                continue

            platform, chat_id = await self._resolve_delivery_target(task)
            if not platform or not chat_id:
                await self._schedule_retry(
                    task,
                    reason="missing_delivery_target",
                )
                continue

            result_obj = await dispatch_queue.latest_result(task.task_id)
            result_dict = (
                result_obj.to_dict()
                if result_obj
                else {"ok": False, "error": task.error}
            )
            delivered = await self._deliver_task(
                platform=platform,
                chat_id=chat_id,
                task=task,
                result=result_dict,
            )
            if delivered:
                await dispatch_queue.clear_relay_retry(task.task_id)
                await dispatch_queue.mark_delivered(task.task_id)
            else:
                await self._schedule_retry(
                    task,
                    reason="delivery_failed",
                )

    @staticmethod
    def _relay_meta(task: TaskEnvelope) -> Dict[str, Any]:
        metadata = dict(task.metadata or {})
        relay = metadata.get("_relay")
        if isinstance(relay, dict):
            return dict(relay)
        return {}

    @staticmethod
    def _parse_iso_ts(value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            return 0.0

    def _is_dead_letter(self, task: TaskEnvelope) -> bool:
        relay = self._relay_meta(task)
        state = str(relay.get("state") or "").strip().lower()
        return state == "dead_letter"

    def _is_backoff_pending(self, task: TaskEnvelope) -> bool:
        relay = self._relay_meta(task)
        state = str(relay.get("state") or "").strip().lower()
        if state != "retrying":
            return False
        next_retry_ts = self._parse_iso_ts(str(relay.get("next_retry_at") or ""))
        return bool(next_retry_ts > time.time())

    def _backoff_sec(self, next_attempt: int) -> float:
        exponent = max(0, int(next_attempt) - 1)
        delay = self.retry_base_sec * (2**exponent)
        return min(self.retry_max_sec, delay)

    async def _schedule_retry(
        self,
        task: TaskEnvelope,
        *,
        reason: str,
    ) -> None:
        relay = self._relay_meta(task)
        current_attempts = max(0, int(relay.get("attempts") or 0))
        next_attempt = current_attempts + 1
        backoff_sec = self._backoff_sec(next_attempt)
        state = await dispatch_queue.bump_relay_retry(
            task_id=task.task_id,
            reason=reason,
            retry_after_sec=backoff_sec,
            max_retries=self.max_retries,
        )
        if not isinstance(state, dict):
            return
        new_state = str(state.get("state") or "").strip().lower()
        if new_state == "dead_letter":
            logger.error(
                "Worker relay dead-letter task=%s reason=%s attempts=%s",
                task.task_id,
                reason,
                int(state.get("attempts") or 0),
            )
        else:
            logger.info(
                "Worker relay retry scheduled task=%s reason=%s attempts=%s backoff=%.1fs",
                task.task_id,
                reason,
                int(state.get("attempts") or 0),
                backoff_sec,
            )

    async def _resolve_delivery_target(self, task: TaskEnvelope) -> tuple[str, str]:
        meta = dict(task.metadata or {})
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

    async def _deliver_task(
        self,
        *,
        platform: str,
        chat_id: str,
        task: TaskEnvelope,
        result: Dict[str, Any],
    ) -> bool:
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            logger.warning(
                "Worker relay skip: adapter missing platform=%s task=%s",
                platform,
                task.task_id,
            )
            return False

        text, ui, files = _build_delivery_text(task, result)
        if not text:
            text = "任务执行完成，但无可展示输出。"

        delivered_any = False
        if files:
            delivered_any = await self._deliver_files(
                adapter=adapter,
                platform=platform,
                chat_id=chat_id,
                files=files,
            )

        chunks = _split_chunks(text)
        if not chunks:
            return delivered_any

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
                    result_obj = send(**kwargs)
                    if inspect.isawaitable(result_obj):
                        await result_obj
                    delivered_any = True
                    continue
                return False
            return delivered_any
        except Exception as exc:
            logger.error(
                "Worker relay delivery failed task=%s platform=%s chat=%s err=%s",
                task.task_id,
                platform,
                chat_id,
                exc,
            )
            return False

    async def _deliver_files(
        self,
        *,
        adapter: Any,
        platform: str,
        chat_id: str,
        files: list[dict[str, str]],
    ) -> bool:
        delivered = False
        for item in files:
            path_text = str(item.get("path") or "").strip()
            if not path_text:
                continue
            path_obj = Path(path_text).expanduser().resolve()
            if not path_obj.exists() or not path_obj.is_file():
                continue
            caption = str(item.get("caption") or "").strip() or None
            filename = (
                str(item.get("filename") or path_obj.name).strip() or path_obj.name
            )
            kind = str(item.get("kind") or "document").strip().lower() or "document"

            actual_path = path_obj
            if kind == "document" and filename.lower().endswith(".md"):
                try:
                    raw_bytes = path_obj.read_bytes()
                    adapted_bytes, adapted_name = adapt_md_file_for_platform(
                        file_bytes=raw_bytes,
                        filename=filename,
                        platform=platform,
                    )
                    if adapted_name != filename:
                        converted_path = path_obj.parent / adapted_name
                        converted_path.write_bytes(adapted_bytes)
                        actual_path = converted_path
                        filename = adapted_name
                except Exception as exc:
                    logger.warning("MD conversion failed, sending original: %s", exc)

            sender = None
            kwargs: Dict[str, Any] = {"chat_id": chat_id}
            if kind == "photo":
                sender = getattr(adapter, "send_photo", None)
                kwargs["photo"] = str(actual_path)
            elif kind == "video":
                sender = getattr(adapter, "send_video", None)
                kwargs["video"] = str(actual_path)
            elif kind == "audio":
                sender = getattr(adapter, "send_audio", None)
                kwargs["audio"] = str(actual_path)

            if not callable(sender):
                sender = getattr(adapter, "send_document", None)
                kwargs = {
                    "chat_id": chat_id,
                    "document": str(actual_path),
                    "filename": filename,
                }

            if not callable(sender):
                continue

            if caption:
                kwargs["caption"] = caption
            result_obj = sender(**kwargs)
            if inspect.isawaitable(result_obj):
                await result_obj
            delivered = True

        return delivered


worker_result_relay = WorkerResultRelay()
