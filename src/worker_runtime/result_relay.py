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


def _parse_iso_timestamp(value: str) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return 0.0


def _extract_payload(
    result: Dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, str]]]:
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

    if file_rows and "files" not in payload_obj:
        payload_obj["files"] = file_rows
    return text, ui, payload_obj, file_rows


def _build_delivery_text(
    job: Dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
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
    text, ui, _payload, files = _extract_payload(result)

    if ok:
        body = text or str(result.get("summary") or "任务执行完成。")
        final_text = f"✅ {worker_name} 已完成任务\n\n{body}".strip()
    else:
        error = str(result.get("error") or job.get("error") or "未知错误").strip()
        summary = str(result.get("summary") or "").strip()
        detail = summary or text or error
        final_text = f"❌ {worker_name} 任务执行失败\n\n{detail}".strip()
    return final_text, ui, files


class WorkerResultRelay:
    """Deliver finished worker job results back to user chats."""

    def __init__(self) -> None:
        self.enabled = (
            os.getenv("WORKER_RESULT_RELAY_ENABLED", "true").strip().lower() == "true"
        )
        self.tick_sec = max(1.0, float(os.getenv("WORKER_RESULT_RELAY_TICK_SEC", "2")))
        self.progress_enabled = (
            os.getenv("WORKER_RESULT_PROGRESS_ENABLED", "true").strip().lower()
            == "true"
        )
        self.progress_notice_sec = max(
            5.0,
            float(os.getenv("WORKER_RESULT_PROGRESS_NOTICE_SEC", "20")),
        )
        self.progress_repeat_sec = max(
            self.progress_notice_sec,
            float(os.getenv("WORKER_RESULT_PROGRESS_REPEAT_SEC", "45")),
        )
        self.progress_stale_sec = max(
            0.0,
            float(os.getenv("WORKER_RESULT_PROGRESS_STALE_SEC", "240")),
        )
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._progress_sent_at: dict[str, float] = {}

    @staticmethod
    def _humanize_tool_name(tool_name: str) -> str:
        raw = str(tool_name or "").strip().lower()
        if not raw:
            return ""
        if raw.startswith("ext_"):
            raw = raw[4:]
        alias = {
            "web_search": "搜索",
            "web_browser": "网页浏览",
            "generate_image": "图片生成",
            "rss_subscribe": "RSS 订阅",
            "stock_watch": "股票行情",
            "reminder": "提醒",
            "deployment_manager": "部署",
        }
        if raw in alias:
            return alias[raw]
        return raw.replace("_", " ")

    @staticmethod
    def _render_progress_detail(progress: Dict[str, Any]) -> str:
        progress_obj = dict(progress) if isinstance(progress, dict) else {}
        running_tool = WorkerResultRelay._humanize_tool_name(
            str(progress_obj.get("running_tool") or "").strip()
        )
        done_tools = [
            WorkerResultRelay._humanize_tool_name(str(item).strip())
            for item in list(progress_obj.get("done_tools") or [])
            if str(item).strip()
        ]
        failed_tools = [
            WorkerResultRelay._humanize_tool_name(str(item).strip())
            for item in list(progress_obj.get("failed_tools") or [])
            if str(item).strip()
        ]

        details: list[str] = []
        if done_tools:
            details.append("已完成：" + " -> ".join(done_tools[-3:]))
        if running_tool:
            details.append(f"正在执行：{running_tool}")
        if failed_tools:
            details.append("失败重试：" + "、".join(failed_tools[-2:]))
        return "；".join(details)

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
        running_ids = await self._deliver_running_progress()
        rows = await worker_task_file_store.list_undelivered(limit=20)
        delivered_ids: set[str] = set()
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
                delivered_ids.add(job_id)
                self._progress_sent_at.pop(job_id, None)

        stale_ids = {
            job_id
            for job_id in list(self._progress_sent_at.keys())
            if job_id not in running_ids and job_id not in delivered_ids
        }
        for job_id in stale_ids:
            self._progress_sent_at.pop(job_id, None)

    async def _deliver_running_progress(self) -> set[str]:
        if not self.progress_enabled:
            return set()

        rows = await worker_task_file_store.list_running(limit=20)
        running_ids: set[str] = set()
        now_ts = time.time()

        for job in rows:
            job_id = str(job.get("job_id") or "").strip()
            if not job_id:
                continue
            running_ids.add(job_id)

            started_ts = _parse_iso_timestamp(
                str(job.get("started_at") or job.get("created_at") or "")
            )
            if started_ts <= 0:
                continue
            elapsed_sec = max(0.0, now_ts - started_ts)
            if elapsed_sec < self.progress_notice_sec:
                continue

            last_sent_at = float(self._progress_sent_at.get(job_id) or 0.0)
            if last_sent_at > 0 and (now_ts - last_sent_at) < self.progress_repeat_sec:
                continue

            metadata = job.get("metadata")
            meta = dict(metadata) if isinstance(metadata, dict) else {}
            platform, chat_id = await self._resolve_delivery_target(meta)
            if not platform or not chat_id:
                continue

            try:
                adapter = adapter_manager.get_adapter(platform)
            except Exception:
                continue

            worker_name = (
                str(
                    meta.get("worker_name") or job.get("worker_id") or "执行助手"
                ).strip()
                or "执行助手"
            )

            progress_obj = meta.get("progress")
            progress_dict = progress_obj if isinstance(progress_obj, dict) else {}
            progress_updated_ts = _parse_iso_timestamp(
                str(progress_dict.get("updated_at") or "")
            )
            if progress_updated_ts <= 0:
                progress_updated_ts = _parse_iso_timestamp(
                    str(job.get("updated_at") or job.get("started_at") or "")
                )
            if (
                self.progress_stale_sec > 0
                and progress_updated_ts > 0
                and (now_ts - progress_updated_ts) > self.progress_stale_sec
            ):
                continue

            elapsed_text = f"{int(elapsed_sec)}秒"
            if elapsed_sec >= 60:
                minutes = int(elapsed_sec // 60)
                seconds = int(elapsed_sec % 60)
                elapsed_text = f"{minutes}分{seconds}秒"
            progress_detail = self._render_progress_detail(progress_dict)
            progress_text = (
                f"⏳ {worker_name} 正在处理中（任务 {job_id}，已执行 {elapsed_text}）。"
            )
            if progress_detail:
                progress_text = f"{progress_text}\n{progress_detail}"
            progress_text = f"{progress_text}\n完成后会自动把结果发给你。"

            send = getattr(adapter, "send_message", None)
            if not callable(send):
                continue
            result = send(chat_id=chat_id, text=progress_text)
            if inspect.isawaitable(result):
                await result
            self._progress_sent_at[job_id] = now_ts

        return running_ids

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

        text, ui, files = _build_delivery_text(job)
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
                    result = send(**kwargs)
                    if inspect.isawaitable(result):
                        await result
                    delivered_any = True
                    continue
                return False
            return delivered_any
        except Exception as exc:
            logger.error(
                "Worker relay delivery failed job=%s platform=%s chat=%s err=%s",
                job.get("job_id"),
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

            # Platform-adaptive format conversion for .md files
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
                        # Write converted file next to original
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
            result = sender(**kwargs)
            if inspect.isawaitable(result):
                await result
            delivered = True

        return delivered


worker_result_relay = WorkerResultRelay()
