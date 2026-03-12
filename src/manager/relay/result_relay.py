from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.file_artifacts import extract_saved_file_rows, normalize_file_rows
from core.heartbeat_store import heartbeat_store
from core.platform.registry import adapter_manager
from core.task_inbox import task_inbox
from manager.relay.closure_service import manager_closure_service
from manager.relay.delivery_policy import (
    delivery_body_mode_for_task,
    delivery_priority_for_task,
    is_heartbeat_delivery,
)
from manager.relay.delivery_store import DeliveryJob, delivery_store
from manager.relay.progress_relay import PROGRESS_EVENTS_TO_DELIVER, build_progress_text
from services.md_converter import adapt_md_file_for_platform
from shared.contracts.dispatch import TaskEnvelope
from shared.queue.dispatch_queue import dispatch_queue

logger = logging.getLogger(__name__)

_RAW_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?:https?://|/)[^)]+\)")
_DELIVERY_PATH_LINE_RE = re.compile(
    r"(?im)^\s*(?:保存路径|文件路径|图片路径|输出路径|附件路径|图片已保存至|saved to|output file|file path)\s*[:：=].*$"
)
_SECTION_HEADER_CANDIDATE_RE = re.compile(r"^[#>*\-\s`]*([^\n]{1,32}?)[#>*\-\s`]*[:：]?\s*$")
_INTERNAL_SECTION_TITLES = {
    "工具选择策略",
    "执行日志",
    "过程记录",
    "处理过程",
    "任务信息",
    "运行日志",
    "思考过程",
}
_FINAL_SECTION_TITLES = {
    "最终结果",
    "报告结果",
    "执行结果",
    "结果",
    "结论",
    "答案",
}
_USER_READY_MARKERS = (
    "适合",
    "建议",
    "今天",
    "明天",
    "成功",
    "失败",
    "已生成",
    "已完成",
)

_VERBOSE_PROGRESS_MARKERS = (
    "## ",
    "```",
    "http://",
    "https://",
    "【搜索结果摘要】",
    "当前：",
    "今天（",
    "明天（",
    "天气情况",
    "图片已生成",
    "保存路径",
    "文件路径",
    "{'ok': true",
    "{'ok': True",
)


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
    file_rows = normalize_file_rows(raw_files)
    if not file_rows and text:
        file_rows = extract_saved_file_rows(text)

    return text, ui, file_rows


def _should_deliver_full_text(result: Dict[str, Any]) -> bool:
    payload = result.get("payload") if isinstance(result, dict) else {}
    payload_obj = dict(payload) if isinstance(payload, dict) else {}
    delivery_mode = str(payload_obj.get("delivery_mode") or "").strip().lower()
    if delivery_mode == "full_text":
        return True
    return bool(payload_obj.get("user_facing_output"))


def _collapse_blank_lines(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    cleaned: list[str] = []
    blank_streak = 0
    for line in lines:
        if not line.strip():
            blank_streak += 1
            if blank_streak > 1:
                continue
        else:
            blank_streak = 0
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _normalize_section_title(line: str) -> str:
    raw = str(line or "").strip()
    if not raw or len(raw) > 40:
        return ""
    match = _SECTION_HEADER_CANDIDATE_RE.match(raw)
    title = str(match.group(1) if match else raw).strip().strip(":：")
    title = re.sub(r"^[^\w\u4e00-\u9fff]+", "", title)
    title = re.sub(r"[^\w\u4e00-\u9fff]+$", "", title)
    return title.strip().lower()


def _find_section_ranges(lines: list[str]) -> list[tuple[int, int, str]]:
    headers: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        title = _normalize_section_title(line)
        if title:
            headers.append((idx, title))

    ranges: list[tuple[int, int, str]] = []
    for pos, (start_idx, title) in enumerate(headers):
        end_idx = len(lines)
        if pos + 1 < len(headers):
            end_idx = headers[pos + 1][0]
        ranges.append((start_idx, end_idx, title))
    return ranges


def _extract_named_section(text: str, *, titles: set[str]) -> str:
    lines = str(text or "").splitlines()
    if not lines:
        return ""
    lowered_titles = {item.lower() for item in titles}
    for start, end, title in _find_section_ranges(lines):
        if title in lowered_titles:
            body_lines = [item.rstrip() for item in lines[start + 1 : end]]
            body = "\n".join(body_lines).strip()
            if body:
                return _collapse_blank_lines(body)
    return ""


def _strip_file_path_lines(text: str, files: list[dict[str, str]]) -> str:
    raw = _DELIVERY_PATH_LINE_RE.sub("", str(text or ""))
    file_markers = {
        str(item.get("path") or "").strip()
        for item in list(files or [])
        if str(item.get("path") or "").strip()
    }
    if not file_markers:
        return _collapse_blank_lines(raw)

    kept: list[str] = []
    for line in raw.splitlines():
        normalized = line.strip().strip("`")
        if normalized in file_markers:
            continue
        kept.append(line)
    return _collapse_blank_lines("\n".join(kept))


def _strip_internal_sections(text: str) -> str:
    lines = str(text or "").splitlines()
    if not lines:
        return ""

    lowered_internal = {item.lower() for item in _INTERNAL_SECTION_TITLES}
    ranges = _find_section_ranges(lines)
    if not ranges:
        return _collapse_blank_lines(text)

    masked = [False] * len(lines)
    for start, end, title in ranges:
        if title in lowered_internal:
            for idx in range(start, end):
                masked[idx] = True

    kept = [line for idx, line in enumerate(lines) if not masked[idx]]
    return _collapse_blank_lines("\n".join(kept))


def _looks_like_raw_worker_output(text: str, files: list[dict[str, str]]) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if any(
        marker in raw
        for marker in ("【搜索结果摘要】", "工具选择策略", "执行日志", "过程记录", "任务编号")
    ):
        return True
    if len(_RAW_MARKDOWN_LINK_RE.findall(raw)) >= 2:
        return True
    if raw.count("\n- [") >= 2:
        return True
    if any(str(item.get("filename") or "").lower().startswith("search_report") for item in files):
        return True
    if len(raw) >= 1000 and not any(marker in raw for marker in _USER_READY_MARKERS):
        return True
    if "traceback" in lowered:
        return True
    return False


def _fallback_delivery_body(
    task: TaskEnvelope,
    result: Dict[str, Any],
    *,
    text: str,
    files: list[dict[str, str]],
) -> str:
    cleaned = _strip_file_path_lines(text, files)
    final_section = _extract_named_section(cleaned, titles=_FINAL_SECTION_TITLES)
    if final_section:
        cleaned = final_section
    else:
        cleaned = _strip_internal_sections(cleaned)

    cleaned = _collapse_blank_lines(cleaned)
    if cleaned and not _looks_like_raw_worker_output(cleaned, files):
        return cleaned

    summary = str(result.get("summary") or "").strip()
    if summary and summary != text:
        summary = _collapse_blank_lines(_strip_file_path_lines(summary, files))
        if summary and not _looks_like_raw_worker_output(summary, files):
            return summary

    goal = str(
        (task.metadata or {}).get("task_goal")
        or (task.metadata or {}).get("original_user_request")
        or task.instruction
        or ""
    ).strip()
    if any(str(item.get("kind") or "").strip().lower() == "document" for item in files):
        if goal:
            return f"已完成关于“{goal[:80]}”的处理，详细报告已附上。"
        return "任务已完成，详细报告已附上。"
    if cleaned:
        return cleaned[:600]
    if goal:
        return f"已完成关于“{goal[:80]}”的处理。"
    return "任务执行完成。"


_NON_REPAIRABLE_FAILURE_MARKERS = (
    "missing required env",
    "environment variable",
    "api key",
    "token",
    "cookie",
    "credential",
    "未配置",
    "环境变量",
    "权限不足",
    "rate limit",
    "dns",
    "network",
    "timeout",
    "登录",
)

_REPAIRABLE_FAILURE_MARKERS = (
    "no such file or directory",
    "can't open file",
    "cannot open",
    "modulenotfounderror",
    "importerror",
    "traceback",
    "unrecognized arguments",
    "syntaxerror",
    "attributeerror",
    "nameerror",
    "typeerror",
    "keyerror",
    "entrypoint",
    "not callable",
    "参数错误",
    "导入失败",
)

_FALLBACK_MARKERS = (
    "fallback",
    "回退",
    "替代方案",
    "workaround",
    "通过 web_extractor",
    "采用了以下替代方案",
)


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
        self.replay_max_age_sec = max(
            60.0,
            float(os.getenv("WORKER_RESULT_RELAY_REPLAY_MAX_AGE_SEC", "900") or 900),
        )
        self.started_at_ts = time.time()
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

    async def _summarize_delivery_body(
        self,
        *,
        task: TaskEnvelope,
        result: Dict[str, Any],
        text: str,
        files: list[dict[str, str]],
    ) -> str:
        fallback = _fallback_delivery_body(task, result, text=text, files=files)
        if fallback and not _looks_like_raw_worker_output(fallback, files) and len(fallback) <= 320:
            return fallback
        if not _looks_like_raw_worker_output(text, files):
            return fallback
        if _is_heartbeat_delivery(task):
            return fallback

        try:
            from core.config import get_client_for_model
            from core.model_config import get_model_for_input, get_routing_model
            from services.openai_adapter import generate_text
        except Exception:
            return fallback

        model_name = str(get_routing_model() or get_model_for_input("text") or "").strip()
        if not model_name:
            return fallback
        client = get_client_for_model(model_name, is_async=True)
        if client is None:
            return fallback

        goal = str(
            (task.metadata or {}).get("task_goal")
            or (task.metadata or {}).get("original_user_request")
            or task.instruction
            or ""
        ).strip()
        report_names = [
            str(item.get("filename") or Path(str(item.get("path") or "")).name).strip()
            for item in list(files or [])
            if str(item.get("filename") or item.get("path") or "").strip()
        ]
        cleaned_text = _strip_internal_sections(_strip_file_path_lines(text, files))
        if not cleaned_text:
            cleaned_text = str(text or "").strip()
        prompt = "\n\n".join(
            [
                f"原始用户任务：{goal or '未提供'}",
                "Worker 原始结果：",
                cleaned_text[:4000],
                "附带文件：" + ("，".join(report_names[:4]) if report_names else "无"),
            ]
        ).strip()
        system_instruction = (
            "你是 X-Bot 的 Core Manager。"
            "请把 Worker 的原始执行结果整理成给最终用户看的简洁中文回复。"
            "要求："
            "1. 只基于给定材料，不要编造；"
            "2. 不要输出工具选择、执行日志、回合、文件系统路径；"
            "3. 若是搜索/网页摘录，优先给出直接结论和 2-5 条关键信息；"
            "4. 若有附件会另外发送，只在必要时简短提示“详细报告见附件”；"
            "5. 输出纯文本，控制在 200 字以内。"
        )
        try:
            rendered = (
                await generate_text(
                    async_client=client,
                    model=model_name,
                    contents=prompt,
                    config={
                        "system_instruction": system_instruction,
                        "temperature": 0.2,
                        "max_output_tokens": 240,
                    },
                )
            ).strip()
        except Exception:
            logger.debug(
                "Worker delivery summary synthesis failed task=%s",
                task.task_id,
                exc_info=True,
            )
            return fallback

        if not rendered:
            return fallback
        return _collapse_blank_lines(rendered)

    async def _build_delivery_text(
        self,
        task: TaskEnvelope,
        result: Dict[str, Any],
        *,
        delivery_priority: str = "interactive",
        body_mode: str = "auto",
    ) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
        ok = bool(result.get("ok"))
        worker_name = str(task.metadata.get("worker_name") or task.worker_id or "执行助手")
        text, ui, files = _extract_payload(result)

        if ok:
            if _should_deliver_full_text(result):
                body = _collapse_blank_lines(
                    _strip_internal_sections(
                        _strip_file_path_lines(
                            text or str(result.get("summary") or ""),
                            files,
                        )
                    )
                )
            elif body_mode == "raw_text" or delivery_priority == "background":
                body = _fallback_delivery_body(
                    task,
                    result,
                    text=text or str(result.get("summary") or ""),
                    files=files,
                )
            else:
                body = await self._summarize_delivery_body(
                    task=task,
                    result=result,
                    text=text or str(result.get("summary") or ""),
                    files=files,
                )
            body = body or str(result.get("summary") or "任务执行完成。")
            final_text = f"✅ {worker_name} 已完成任务\n\n{body}".strip()
        else:
            error = str(result.get("error") or task.error or "未知错误").strip()
            summary = str(result.get("summary") or "").strip()
            detail = _fallback_delivery_body(
                task,
                result,
                text=summary or text or error,
                files=files,
            )
            final_text = f"❌ {worker_name} 任务执行失败\n\n{detail or error}".strip()
        return final_text, ui, files

    @staticmethod
    def _parse_iso_ts(value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            return 0.0

    @staticmethod
    def _extract_skill_from_instruction(task: TaskEnvelope) -> Dict[str, Any]:
        raw_text = " ".join(
            [
                str(task.instruction or ""),
                str((task.metadata or {}).get("task_goal") or ""),
            ]
        ).lower()
        if not raw_text:
            return {}

        path_match = re.search(r"/skills/learned/([a-z0-9_\-]+)", raw_text)
        path_hint = str(path_match.group(1) or "").strip() if path_match else ""

        try:
            from core.skill_loader import skill_loader

            for info in skill_loader.get_skills_summary():
                skill_name = str(info.get("name") or "").strip()
                if not skill_name:
                    continue
                skill_obj = skill_loader.get_skill(skill_name) or {}
                if str(skill_obj.get("source") or "").strip() != "learned":
                    continue
                aliases = {
                    skill_name.lower(),
                    skill_name.lower().replace("_", "-"),
                    skill_name.lower().replace("-", "_"),
                    str(Path(str(skill_obj.get("skill_dir") or "")).name).strip().lower(),
                }
                aliases = {token for token in aliases if token}
                if path_hint and path_hint in aliases:
                    return skill_obj
                if any(alias and alias in raw_text for alias in aliases):
                    return skill_obj
        except Exception:
            return {}
        return {}

    @staticmethod
    def _result_text_blob(task: TaskEnvelope, result: Dict[str, Any]) -> str:
        payload = result.get("payload") if isinstance(result, dict) else {}
        payload_obj = payload if isinstance(payload, dict) else {}
        progress = dict((task.metadata or {}).get("progress") or {})
        chunks = [
            str(result.get("summary") or ""),
            str(result.get("error") or ""),
            str(payload_obj.get("text") or ""),
            str(progress.get("final_preview") or ""),
        ]
        return "\n".join([item for item in chunks if str(item).strip()]).lower()

    def _should_auto_repair_skill(
        self,
        *,
        task: TaskEnvelope,
        result: Dict[str, Any],
        skill: Dict[str, Any],
    ) -> bool:
        if str(skill.get("source") or "").strip() != "learned":
            return False

        blob = self._result_text_blob(task, result)
        if not blob:
            return False
        if any(marker in blob for marker in _NON_REPAIRABLE_FAILURE_MARKERS):
            return False
        if any(marker in blob for marker in _REPAIRABLE_FAILURE_MARKERS):
            return True

        progress = dict((task.metadata or {}).get("progress") or {})
        failed_tools = [
            str(item or "").strip().lower()
            for item in list(progress.get("failed_tools") or [])
            if str(item or "").strip()
        ]
        if bool(result.get("ok")) and failed_tools and any(
            marker in blob for marker in _FALLBACK_MARKERS
        ):
            return True
        return False

    @staticmethod
    async def _recent_auto_repair_exists(skill_name: str) -> bool:
        safe_skill = str(skill_name or "").strip()
        if not safe_skill:
            return False
        try:
            from manager.dev.task_store import dev_task_store

            rows = await dev_task_store.list_recent(limit=40)
        except Exception:
            return False

        now_ts = time.time()
        for row in rows:
            if not isinstance(row, dict):
                continue
            template = dict(row.get("template") or {})
            if str(template.get("skill_name") or "").strip() != safe_skill:
                continue
            if str(template.get("source") or "").strip() != "worker_skill_auto_repair":
                continue
            updated_ts = WorkerResultRelay._parse_iso_ts(str(row.get("updated_at") or ""))
            if updated_ts and now_ts - updated_ts <= 900:
                return True
        return False

    async def _maybe_trigger_skill_auto_repair(
        self,
        *,
        task: TaskEnvelope,
        result: Dict[str, Any],
        platform: str,
        chat_id: str,
    ) -> str:
        skill = self._extract_skill_from_instruction(task)
        skill_name = str(skill.get("name") or "").strip()
        skill_dir = str(skill.get("skill_dir") or "").strip()
        if not skill_name or not skill_dir:
            return ""
        if not self._should_auto_repair_skill(task=task, result=result, skill=skill):
            return ""
        if await self._recent_auto_repair_exists(skill_name):
            return ""

        user_id = str((task.metadata or {}).get("user_id") or "").strip()
        excerpt = _split_chunks(self._result_text_blob(task, result), limit=500)
        failure_excerpt = excerpt[0] if excerpt else ""
        instruction = "\n".join(
            [
                f"请修复技能 `{skill_name}`，让 Worker 下次可以直接调用成功。",
                "保留上游实现，优先修入口、兼容层、SKILL.md 或参数适配，不要重写整个技能。",
                "",
                "原始用户任务：",
                str(task.instruction or "").strip(),
                "",
                "最近失败/回退线索：",
                failure_excerpt or "worker 调用该 skill 时出现运行失败或降级回退。",
            ]
        ).strip()

        try:
            from manager.dev.service import manager_dev_service

            repair = await manager_dev_service.software_delivery(
                action="skill_modify",
                skill_name=skill_name,
                instruction=instruction,
                cwd=skill_dir,
                backend=str(os.getenv("CODING_BACKEND_DEFAULT", "codex") or "codex").strip(),
                source="worker_skill_auto_repair",
                notify_platform=platform,
                notify_chat_id=chat_id,
                notify_user_id=user_id,
            )
        except Exception:
            logger.debug(
                "Worker relay auto repair dispatch failed task=%s skill=%s",
                task.task_id,
                skill_name,
                exc_info=True,
            )
            return ""

        if not bool(repair.get("ok")):
            return ""
        return str(repair.get("task_id") or "").strip()

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Worker result relay disabled by env.")
            return
        if self._loop_task and not self._loop_task.done():
            return
        self.started_at_ts = time.time()
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
        await self._process_running_progress()
        await self._ensure_delivery_jobs()

        jobs = await delivery_store.list_ready(limit=20)
        for job in jobs:
            task = await dispatch_queue.get_task(job.task_id)
            if task is None:
                continue

            platform, chat_id = await self._resolve_delivery_target(task)
            if not platform or not chat_id:
                await self._schedule_retry(
                    task,
                    reason="missing_delivery_target",
                )
                retry_state = await delivery_store.get(task.task_id)
                await self._sync_session_delivery_state(
                    task=task,
                    delivery_state=str(getattr(retry_state, "status", "retrying") or "retrying"),
                )
                continue

            await delivery_store.ensure_job(
                task=task,
                priority=str(job.priority or self._delivery_priority(task)),
                body_mode=str(job.body_mode or self._delivery_body_mode(task)),
                target_platform=platform,
                target_chat_id=chat_id,
            )

            await self._drain_task_progress(
                task=task,
                platform=platform,
                chat_id=chat_id,
            )

            result_obj = await dispatch_queue.latest_result(task.task_id)
            result_dict = (
                result_obj.to_dict()
                if result_obj
                else {"ok": False, "error": task.error}
            )
            prepared = await self._prepare_delivery_content(
                platform=platform,
                chat_id=chat_id,
                task=task,
                result=result_dict,
                delivery_job=job,
            )
            delivered = await self._deliver_task(
                platform=platform,
                chat_id=chat_id,
                task=task,
                result=result_dict,
                delivery_job=job,
                prepared=prepared,
            )
            if delivered:
                await delivery_store.mark_delivered(
                    task.task_id,
                    summary=str(prepared.get("text") or "")[:3000],
                )
                await dispatch_queue.mark_delivered(task.task_id)
                await self._sync_session_delivery_state(
                    task=task,
                    delivery_state="delivered",
                    summary=str(prepared.get("text") or "")[:2400],
                )
            else:
                await self._schedule_retry(
                    task,
                    reason="delivery_failed",
                )
                retry_state = await delivery_store.get(task.task_id)
                await self._sync_session_delivery_state(
                    task=task,
                    delivery_state=str(getattr(retry_state, "status", "retrying") or "retrying"),
                )

    async def _process_running_progress(self) -> None:
        list_running = getattr(dispatch_queue, "list_running", None)
        ack_progress_events = getattr(dispatch_queue, "ack_progress_events", None)
        if not callable(list_running) or not callable(ack_progress_events):
            return

        running_tasks = await list_running(limit=20)
        for task in running_tasks:
            platform, chat_id = await self._resolve_delivery_target(task)
            if not platform or not chat_id:
                continue
            await self._drain_task_progress(
                task=task,
                platform=platform,
                chat_id=chat_id,
            )

    async def _drain_task_progress(
        self,
        *,
        task: TaskEnvelope,
        platform: str,
        chat_id: str,
    ) -> None:
        ack_progress_events = getattr(dispatch_queue, "ack_progress_events", None)
        if not callable(ack_progress_events):
            return

        metadata = dict(task.metadata or {})
        raw_events = metadata.get("progress_events")
        progress_events = [
            dict(item)
            for item in list(raw_events or [])
            if isinstance(item, dict)
        ]
        if not progress_events:
            return

        delivered_upto_seq = 0
        delivered_last_event = ""
        for item in progress_events:
            seq = max(0, int(item.get("seq") or 0))
            delivered_upto_seq = max(delivered_upto_seq, seq)
            event_name = str(item.get("event") or "").strip().lower()
            delivered_last_event = event_name or delivered_last_event
            if event_name not in PROGRESS_EVENTS_TO_DELIVER:
                continue

            text = build_progress_text(task, item)
            sent = await self._deliver_progress(
                task=task,
                platform=platform,
                chat_id=chat_id,
                text=text,
            )
            if not sent:
                delivered_upto_seq = max(0, seq - 1)
                break

        if delivered_upto_seq > 0:
            await ack_progress_events(
                task.task_id,
                upto_seq=delivered_upto_seq,
                last_event=delivered_last_event,
            )

    @staticmethod
    def _relay_meta(task: TaskEnvelope) -> Dict[str, Any]:
        metadata = dict(task.metadata or {})
        relay = metadata.get("_relay")
        if isinstance(relay, dict):
            return dict(relay)
        return {}

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

    @staticmethod
    def _delivery_priority(task: TaskEnvelope) -> str:
        return delivery_priority_for_task(task)

    def _delivery_body_mode(self, task: TaskEnvelope) -> str:
        return delivery_body_mode_for_task(task)

    def _task_finished_ts(self, task: TaskEnvelope) -> float:
        for raw in (task.ended_at, task.updated_at, task.created_at):
            ts = self._parse_iso_ts(str(raw or ""))
            if ts > 0:
                return ts
        return 0.0

    def _should_suppress_startup_replay(
        self,
        *,
        task: TaskEnvelope,
        existing_job: DeliveryJob | None,
    ) -> bool:
        if existing_job is not None and str(existing_job.status or "").strip().lower() in {
            "delivered",
            "suppressed",
        }:
            return False
        relay = self._relay_meta(task)
        if str(relay.get("state") or "").strip().lower() == "dead_letter":
            return True
        finished_ts = self._task_finished_ts(task)
        if finished_ts <= 0:
            return False
        if finished_ts >= max(0.0, self.started_at_ts - 5.0):
            return False
        age_sec = max(0.0, time.time() - finished_ts)
        return age_sec > self.replay_max_age_sec

    async def _schedule_retry(
        self,
        task: TaskEnvelope,
        *,
        reason: str,
    ) -> None:
        existing = await delivery_store.get(task.task_id)
        current_attempts = max(0, int(getattr(existing, "attempts", 0) or 0))
        next_attempt = current_attempts + 1
        backoff_sec = self._backoff_sec(next_attempt)
        state = await delivery_store.schedule_retry(
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

    async def _sync_session_delivery_state(
        self,
        *,
        task: TaskEnvelope,
        delivery_state: str,
        summary: str = "",
    ) -> None:
        metadata = dict(task.metadata or {})
        task_inbox_id = str(metadata.get("task_inbox_id") or "").strip()
        safe_state = str(delivery_state or "").strip().lower()
        safe_summary = str(summary or "").strip()[:2400]
        if task_inbox_id:
            try:
                session_task = await task_inbox.get(task_inbox_id)
                if session_task is not None:
                    merged_metadata = dict(session_task.metadata or {})
                    if (
                        str(merged_metadata.get("delivery_state") or "").strip().lower()
                        != safe_state
                        or (
                            safe_summary
                            and merged_metadata.get("last_user_visible_summary") != safe_summary
                        )
                    ):
                        merged_metadata["delivery_state"] = safe_state
                        if safe_summary:
                            merged_metadata["last_user_visible_summary"] = safe_summary
                        await task_inbox.update_status(
                            task_inbox_id,
                            session_task.status,
                            event="delivery_state_changed",
                            detail=safe_state[:200],
                            metadata=merged_metadata,
                        )
            except Exception:
                logger.debug(
                    "Failed to sync task inbox delivery state task=%s state=%s",
                    task.task_id,
                    safe_state,
                    exc_info=True,
                )

        user_id = str(metadata.get("user_id") or "").strip()
        if not user_id:
            return
        try:
            active_task = await heartbeat_store.get_session_active_task(user_id)
            if not active_task:
                return
            active_session = (
                str(active_task.get("session_task_id") or "").strip()
                or str(active_task.get("task_inbox_id") or "").strip()
            )
            task_session = (
                str(metadata.get("session_task_id") or "").strip()
                or str(metadata.get("task_inbox_id") or "").strip()
            )
            if active_session and task_session and active_session != task_session:
                return
            fields: Dict[str, Any] = {}
            if str(active_task.get("delivery_state") or "").strip().lower() != safe_state:
                fields["delivery_state"] = safe_state
            if safe_summary and active_task.get("last_user_visible_summary") != safe_summary:
                fields["last_user_visible_summary"] = safe_summary
            if fields:
                await heartbeat_store.update_session_active_task(user_id, **fields)
        except Exception:
            logger.debug(
                "Failed to sync heartbeat delivery state task=%s state=%s",
                task.task_id,
                safe_state,
                exc_info=True,
            )

    async def _ensure_delivery_jobs(self) -> None:
        rows = await dispatch_queue.list_undelivered(limit=20)
        for task in rows:
            existing_job = await delivery_store.get(task.task_id)
            if self._should_suppress_startup_replay(
                task=task,
                existing_job=existing_job,
            ):
                await delivery_store.ensure_job(
                    task=task,
                    priority=self._delivery_priority(task),
                    body_mode=self._delivery_body_mode(task),
                )
                await delivery_store.mark_suppressed(
                    task.task_id,
                    reason="startup_replay_suppressed",
                    summary="suppressed stale undelivered task on relay startup",
                )
                await dispatch_queue.mark_delivered(task.task_id)
                await self._sync_session_delivery_state(
                    task=task,
                    delivery_state="suppressed",
                )
                continue
            await delivery_store.ensure_job(
                task=task,
                priority=self._delivery_priority(task),
                body_mode=self._delivery_body_mode(task),
            )
            await self._sync_session_delivery_state(
                task=task,
                delivery_state="pending",
            )

    async def _prepare_delivery_content(
        self,
        *,
        platform: str,
        chat_id: str,
        task: TaskEnvelope,
        result: Dict[str, Any],
        delivery_job: DeliveryJob | None = None,
    ) -> Dict[str, Any]:
        closure = await manager_closure_service.resolve_attempt(
            task=task,
            result=result,
            platform=platform,
            chat_id=chat_id,
        )
        closure_kind = str(closure.get("kind") or "").strip().lower()
        job_priority = (
            str(getattr(delivery_job, "priority", "") or "").strip().lower()
            or self._delivery_priority(task)
        )
        body_mode = (
            str(getattr(delivery_job, "body_mode", "") or "").strip().lower()
            or self._delivery_body_mode(task)
        )
        if closure_kind == "legacy":
            text, ui, files = await self._build_delivery_text(
                task,
                result,
                delivery_priority=job_priority,
                body_mode=body_mode,
            )
        elif closure_kind == "final":
            text, ui, files = await self._build_delivery_text(
                task,
                dict(closure.get("result") or result),
                delivery_priority=job_priority,
                body_mode=body_mode,
            )
        else:
            text = str(closure.get("text") or "").strip()
            ui = dict(closure.get("ui") or {})
            files = normalize_file_rows(closure.get("files") or [])
        if not text:
            text = "任务执行完成，但无可展示输出。"

        repair_task_id = ""
        if bool(closure.get("auto_repair_allowed", closure_kind == "legacy")):
            repair_task_id = await self._maybe_trigger_skill_auto_repair(
                task=task,
                result=result,
                platform=platform,
                chat_id=chat_id,
            )
        if repair_task_id:
            if closure_kind == "waiting_user":
                text = (
                    text.rstrip()
                    + "\n\n🛠 已自动发起技能修复任务 "
                    + f"`{repair_task_id}`"
                    + "，但当前原任务仍保留，你可以回复“继续”或补充说明后继续推进。"
                ).strip()
            else:
                text = (
                    text.rstrip()
                    + "\n\n🛠 已自动发起技能修复任务 "
                    + f"`{repair_task_id}`"
                    + "，Manager 会继续把这个 skill 修到可直接调用。"
                ).strip()
        return {
            "text": text,
            "ui": ui,
            "files": files,
            "closure_kind": closure_kind,
        }

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
        delivery_job: DeliveryJob | None = None,
        prepared: Dict[str, Any] | None = None,
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

        prepared_obj = dict(prepared or {})
        if not prepared_obj:
            prepared_obj = await self._prepare_delivery_content(
                platform=platform,
                chat_id=chat_id,
                task=task,
                result=result,
                delivery_job=delivery_job,
            )
        text = str(prepared_obj.get("text") or "").strip()
        ui = dict(prepared_obj.get("ui") or {})
        files = normalize_file_rows(prepared_obj.get("files") or [])
        if not text:
            text = "任务执行完成，但无可展示输出。"

        delivered_any = False
        chunks = _split_chunks(text)
        if not chunks:
            if files:
                return await self._deliver_files(
                    adapter=adapter,
                    platform=platform,
                    chat_id=chat_id,
                    files=files,
                )
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
            if files:
                delivered_any = (
                    await self._deliver_files(
                        adapter=adapter,
                        platform=platform,
                        chat_id=chat_id,
                        files=files,
                    )
                    or delivered_any
                )
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

    async def _deliver_progress(
        self,
        *,
        task: TaskEnvelope,
        platform: str,
        chat_id: str,
        text: str,
    ) -> bool:
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            return False

        payload = str(text or "").strip()
        if not payload:
            return True

        send_draft = getattr(adapter, "send_message_draft", None)
        if platform == "telegram" and callable(send_draft):
            try:
                metadata = dict(task.metadata or {})
                raw_thread_id = metadata.get("message_thread_id")
                message_thread_id = (
                    int(raw_thread_id)
                    if str(raw_thread_id or "").strip()
                    else None
                )
                draft_seed = (
                    str(metadata.get("user_visible_task_id") or "").strip()
                    or str(metadata.get("session_task_id") or "").strip()
                    or str(metadata.get("task_inbox_id") or "").strip()
                    or str(task.task_id or "").strip()
                )
                draft_id = max(
                    1,
                    int(zlib.crc32(draft_seed.encode("utf-8")) & 0x7FFFFFFF),
                )
                result_obj = send_draft(
                    chat_id=chat_id,
                    draft_id=draft_id,
                    text=payload,
                    message_thread_id=message_thread_id,
                )
                if inspect.isawaitable(result_obj):
                    await result_obj
                return True
            except Exception as exc:
                logger.warning(
                    "Worker relay progress draft failed task=%s platform=%s chat=%s err=%s",
                    task.task_id,
                    platform,
                    chat_id,
                    exc,
                )

        send = getattr(adapter, "send_message", None)
        if not callable(send):
            return False

        try:
            result_obj = send(chat_id=chat_id, text=payload)
            if inspect.isawaitable(result_obj):
                await result_obj
            return True
        except Exception as exc:
            logger.warning(
                "Worker relay progress delivery failed platform=%s chat=%s err=%s",
                platform,
                chat_id,
                exc,
            )
            return False


worker_result_relay = WorkerResultRelay()
