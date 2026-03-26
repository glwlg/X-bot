import asyncio
import contextlib
import inspect
import logging
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.agent_input import MAX_INLINE_IMAGE_INPUTS, build_agent_message_history
from core.agent_orchestrator import agent_orchestrator
from core.background_delivery import push_background_text, split_background_chunks
from core.file_artifacts import extract_saved_file_rows, merge_file_rows, normalize_file_rows
from core.heartbeat_store import heartbeat_store
from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from core.platform.registry import adapter_manager
from core.runtime_callbacks import pop_runtime_callback, set_runtime_callback
from core.task_manager import task_manager

logger = logging.getLogger(__name__)
_DEFAULT_HEARTBEAT_GOAL = "检查自己和后台任务的运行状态是否良好"


class _HeartbeatSilentAdapter:
    """Swallow in-run replies to avoid noisy intermediate messages."""

    async def reply_text(self, ctx: UnifiedContext, text: str, ui=None, **kwargs):
        return SimpleNamespace(id=f"hb-silent-{int(datetime.now().timestamp())}")

    async def edit_text(
        self, ctx: UnifiedContext, message_id: str, text: str, **kwargs
    ):
        return SimpleNamespace(id=message_id)

    async def reply_document(
        self, ctx: UnifiedContext, document, filename=None, caption=None, **kwargs
    ):
        return SimpleNamespace(id=filename or "doc")

    async def reply_photo(self, ctx: UnifiedContext, photo, caption=None, **kwargs):
        return SimpleNamespace(id="photo")

    async def reply_video(self, ctx: UnifiedContext, video, caption=None, **kwargs):
        return SimpleNamespace(id="video")

    async def reply_audio(self, ctx: UnifiedContext, audio, caption=None, **kwargs):
        return SimpleNamespace(id="audio")

    async def delete_message(
        self, ctx: UnifiedContext, message_id: str, chat_id=None, **kwargs
    ):
        return True

    async def send_chat_action(
        self, ctx: UnifiedContext, action: str, chat_id=None, **kwargs
    ):
        return True

    async def download_file(self, ctx: UnifiedContext, file_id: str, **kwargs) -> bytes:
        raise RuntimeError("heartbeat daemon context does not support file download")


class HeartbeatWorker:
    def __init__(self):
        enabled_raw = os.getenv("HEARTBEAT_ENABLED")
        if enabled_raw is None:
            enabled_raw = os.getenv("HEARTBEAT_WORKER_ENABLED", "true")
        self.enabled = str(enabled_raw).lower() == "true"

        tick_raw = os.getenv("HEARTBEAT_TICK_SEC", "30")
        self.tick_sec = max(5, int(tick_raw))

        self.suppress_ok = os.getenv("HEARTBEAT_SUPPRESS_OK", "true").lower() == "true"
        self.mode = os.getenv("HEARTBEAT_MODE", "execute").strip().lower() or "execute"
        self.enable_rss_signal = (
            os.getenv("HEARTBEAT_RSS_SIGNAL_ENABLED", "true").lower() == "true"
        )
        self.enable_stock_signal = (
            os.getenv("HEARTBEAT_STOCK_SIGNAL_ENABLED", "false").lower() == "true"
        )
        self.push_file_enabled = (
            os.getenv("HEARTBEAT_PUSH_FILE_ENABLED", "true").lower() == "true"
        )
        try:
            push_threshold = int(os.getenv("HEARTBEAT_PUSH_FILE_THRESHOLD", "12000"))
        except Exception:
            push_threshold = 12000
        self.push_file_threshold = max(512, push_threshold)
        try:
            max_chunks = int(os.getenv("HEARTBEAT_PUSH_MAX_TEXT_CHUNKS", "3"))
        except Exception:
            max_chunks = 3
        self.push_max_text_chunks = max(1, max_chunks)
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._running: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Heartbeat worker disabled by env.")
            return
        if self._loop_task and not self._loop_task.done():
            return
        compacted = await heartbeat_store.compact_all_users()
        if compacted:
            logger.info(
                "Heartbeat store compacted for %s user(s) on startup.", compacted
            )
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="heartbeat-worker-loop"
        )
        logger.info(
            "Heartbeat worker started. root=%s tick=%ss",
            heartbeat_store.root,
            self.tick_sec,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        self._loop_task = None

        for task in list(self._running.values()):
            task.cancel()
        if self._running:
            with contextlib.suppress(Exception):
                await asyncio.gather(*self._running.values(), return_exceptions=True)
        self._running.clear()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Heartbeat worker loop error: %s", exc, exc_info=True)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.tick_sec)
            except asyncio.TimeoutError:
                continue

    async def process_once(self) -> None:
        if not self.enabled:
            return

        users = await heartbeat_store.list_users()
        for user_id in users:
            if task_manager.has_active_task(user_id):
                continue
            if user_id in self._running:
                continue
            should_run = await heartbeat_store.should_run_heartbeat(user_id)
            if not should_run:
                continue

            task = asyncio.create_task(
                self._run_heartbeat_for_user(user_id, force=False),
                name=f"heartbeat-run-{user_id}-{int(datetime.now().timestamp())}",
            )
            self._running[user_id] = task
            task.add_done_callback(lambda _t, uid=user_id: self._running.pop(uid, None))

    async def run_user_now(self, user_id: str, *, suppress_push: bool = False) -> str:
        """Manual trigger for /heartbeat run."""
        user_id = str(user_id)
        if user_id in self._running:
            return "Heartbeat already running."
        return await self._run_heartbeat_for_user(
            user_id,
            force=True,
            suppress_push=bool(suppress_push),
        )

    async def _run_heartbeat_for_user(
        self,
        user_id: str,
        force: bool,
        *,
        suppress_push: bool = False,
    ) -> str:
        user_id = str(user_id)
        owner = f"hb:{user_id}:{int(datetime.now().timestamp())}"
        locked = await heartbeat_store.claim_lock(user_id, owner=owner)
        if not locked:
            return "lock_busy"

        current = asyncio.current_task()
        if current is not None:
            await task_manager.register_task(
                user_id,
                current,
                description="Heartbeat 周期检查",
                heartbeat_path=str(heartbeat_store.heartbeat_path(user_id)),
                task_id=f"hb-{int(datetime.now().timestamp())}",
            )

        try:
            if not force:
                should_run = await heartbeat_store.should_run_heartbeat(user_id)
                if not should_run:
                    return "skipped"

            spec = await heartbeat_store.get_heartbeat_spec(user_id)
            checklist = list(spec.get("checklist") or [])
            checklist_items = list(spec.get("checklist_items") or [])
            delivery_target = await heartbeat_store.get_delivery_target(user_id)
            batch_result = await self._run_heartbeat_task_batch_details(
                user_id=user_id,
                checklist=checklist,
                checklist_items=checklist_items,
                owner=owner,
                delivery_target=delivery_target,
            )
            final_text = str(batch_result.get("text") or "").strip() or "HEARTBEAT_OK"

            if "已派发给" in final_text and "完成后会自动把结果发给你" in final_text:
                final_text = "HEARTBEAT_OK"

            level = str(batch_result.get("level") or "").strip().upper()
            if level not in {"OK", "NOTICE", "ACTION"}:
                level = heartbeat_store.classify_result(final_text)

            heartbeat_meta = await heartbeat_store.mark_heartbeat_run(
                user_id,
                final_text,
                level=level,
            )
            with contextlib.suppress(Exception):
                from core.long_term_memory import long_term_memory

                rollup_result = await long_term_memory.rollup_today_sessions(
                    user_id
                )
                logger.info(
                    "Heartbeat daily rollup user=%s result=%s", user_id, rollup_result
                )
            await heartbeat_store.clear_last_error(user_id)

            if final_text.strip() == "HEARTBEAT_OK" and self.suppress_ok:
                return "HEARTBEAT_OK"

            level = str(heartbeat_meta.get("last_level", level)).upper()
            deliveries = list(batch_result.get("deliveries") or [])
            logger.info(
                "Heartbeat push planning user=%s suppress_push=%s deliveries=%s details=%s",
                user_id,
                bool(suppress_push),
                len(deliveries),
                [
                    {
                        "platform": str(item.get("platform") or "").strip(),
                        "chat_id": str(item.get("chat_id") or "").strip(),
                        "text_len": len(str(item.get("text") or "").strip()),
                        "raw_files": (
                            len(item.get("files"))
                            if isinstance(item.get("files"), list)
                            else 0
                        ),
                    }
                    for item in deliveries
                    if isinstance(item, dict)
                ],
            )
            if not deliveries:
                logger.info(
                    "Heartbeat result skipped push: no delivery target for user=%s",
                    user_id,
                )
                return final_text

            for target in deliveries:
                platform = str(target.get("platform") or "").strip()
                chat_id = str(target.get("chat_id") or "").strip()
                text_to_push = str(target.get("text") or "").strip()
                raw_files = target.get("files")
                files = normalize_file_rows(raw_files)
                logger.info(
                    "Heartbeat delivery target user=%s platform=%s chat=%s text_len=%s raw_files=%s normalized_files=%s",
                    user_id,
                    platform,
                    chat_id,
                    len(text_to_push),
                    len(raw_files) if isinstance(raw_files, list) else 0,
                    len(files),
                )
                session_id = ""
                if (
                    platform
                    and chat_id
                    and platform == str(delivery_target.get("platform", "") or "").strip()
                    and chat_id == str(delivery_target.get("chat_id", "") or "").strip()
                ):
                    session_id = str(delivery_target.get("session_id", "") or "").strip()
                if not platform or not chat_id or (not text_to_push and not files):
                    continue
                pushed = False
                attempted = False
                if text_to_push and not suppress_push:
                    attempted = True
                    pushed = await self._push_to_target(
                        platform=platform,
                        chat_id=chat_id,
                        text=text_to_push,
                        user_id=user_id,
                        session_id=session_id,
                    )
                if files:
                    attempted = True
                    pushed = (
                        await self._push_files_to_target(
                            platform=platform,
                            chat_id=chat_id,
                            files=files,
                        )
                        or pushed
                    )
                if attempted and not pushed:
                    logger.warning(
                        "Heartbeat result push failed. user=%s platform=%s chat=%s",
                        user_id,
                        platform,
                        chat_id,
                    )
            return final_text
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Heartbeat user run error: user=%s err=%s", user_id, exc, exc_info=True
            )
            await heartbeat_store.set_last_error(user_id, str(exc))
            await heartbeat_store.mark_heartbeat_run(
                user_id,
                f"ERROR: {exc}",
                level="ACTION",
            )
            return f"ERROR: {exc}"
        finally:
            await heartbeat_store.release_lock(user_id, owner=owner)
            if current is not None:
                task_manager.unregister_task(user_id)

    async def _run_heartbeat_task_batch(
        self,
        *,
        user_id: str,
        checklist: list[str],
        owner: str,
        delivery_target: dict[str, str] | None = None,
        checklist_items: list[dict[str, Any]] | None = None,
    ) -> str:
        result = await self._run_heartbeat_task_batch_details(
            user_id=user_id,
            checklist=checklist,
            checklist_items=checklist_items,
            owner=owner,
            delivery_target=delivery_target,
        )
        return str(result.get("text") or "").strip() or "HEARTBEAT_OK"

    @staticmethod
    def _normalize_delivery_target(
        target: dict[str, Any] | None,
    ) -> dict[str, str]:
        payload = dict(target or {})
        return {
            "platform": str(payload.get("platform") or "").strip(),
            "chat_id": str(payload.get("chat_id") or "").strip(),
        }

    @staticmethod
    def _target_key(target: dict[str, str]) -> tuple[str, str]:
        return (
            str(target.get("platform") or "").strip(),
            str(target.get("chat_id") or "").strip(),
        )

    @classmethod
    def _append_delivery_section(
        cls,
        deliveries: dict[tuple[str, str], list[str]],
        target: dict[str, str] | None,
        text: str,
    ) -> None:
        normalized_target = cls._normalize_delivery_target(target)
        key = cls._target_key(normalized_target)
        if not key[0] or not key[1]:
            return
        body = str(text or "").strip()
        if not body:
            return
        bucket = deliveries.setdefault(key, [])
        if body not in bucket:
            bucket.append(body)

    @classmethod
    def _append_delivery_files(
        cls,
        deliveries: dict[tuple[str, str], list[dict[str, str]]],
        target: dict[str, str] | None,
        files: list[dict[str, str]],
    ) -> None:
        normalized_target = cls._normalize_delivery_target(target)
        key = cls._target_key(normalized_target)
        if not key[0] or not key[1]:
            return
        merged = merge_file_rows(
            deliveries.get(key, []),
            normalize_file_rows(files),
        )
        if merged:
            deliveries[key] = merged

    async def _run_heartbeat_task_batch_details(
        self,
        *,
        user_id: str,
        checklist: list[str],
        owner: str,
        delivery_target: dict[str, str] | None = None,
        checklist_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        specs = await self._build_heartbeat_task_specs(
            user_id=user_id,
            checklist=checklist,
            checklist_items=checklist_items,
            default_delivery_target=delivery_target,
        )
        if not specs:
            return {"text": "HEARTBEAT_OK", "deliveries": []}

        ctx = self._build_headless_context(user_id)
        ctx.user_data["heartbeat_session_state_enabled"] = True
        if self.mode == "readonly":
            ctx.user_data["execution_policy"] = "heartbeat_readonly_policy"
        else:
            ctx.user_data["execution_policy"] = "ikaros_execution_policy"

        sections: list[str] = []
        routed_sections: dict[tuple[str, str], list[str]] = {}
        routed_files: dict[tuple[str, str], list[dict[str, str]]] = {}
        rss_refresh_attempted = False
        rss_refresh_available = False
        rss_refresh_text = ""
        rss_refresh_appended = False
        overall_level = "OK"
        level_rank = {"OK": 0, "NOTICE": 1, "ACTION": 2}
        for idx, spec in enumerate(specs, start=1):
            await heartbeat_store.refresh_lock(user_id, owner=owner)
            title = str(spec.get("title") or f"任务 {idx}")
            goal = str(spec.get("goal") or "").strip()
            if not goal:
                continue
            spec_target = self._normalize_delivery_target(
                spec.get("delivery_target")
                if isinstance(spec, dict)
                else delivery_target
            )
            if spec_target["platform"]:
                ctx.user_data["subagent_delivery_platform"] = spec_target["platform"]
            else:
                ctx.user_data.pop("subagent_delivery_platform", None)
            if spec_target["chat_id"]:
                ctx.user_data["subagent_delivery_chat_id"] = spec_target["chat_id"]
            else:
                ctx.user_data.pop("subagent_delivery_chat_id", None)

            spec_type = str(spec.get("type") or "").strip().lower()
            if spec_type == "rss_signal":
                if not rss_refresh_attempted:
                    rss_refresh_attempted = True
                    try:
                        from extension.skills.registry import skill_registry as skill_loader

                        rss_module = skill_loader.import_skill_module("rss_subscribe")
                        trigger_manual_rss_check = getattr(
                            rss_module,
                            "trigger_manual_rss_check",
                            None,
                        )
                        if not callable(trigger_manual_rss_check):
                            raise RuntimeError(
                                "rss_subscribe.trigger_manual_rss_check unavailable"
                            )

                        rss_refresh_text = str(
                            await trigger_manual_rss_check(
                                user_id,
                                suppress_busy_message=True,
                            )
                            or ""
                        ).strip()
                        rss_refresh_available = True
                    except Exception as exc:
                        logger.warning(
                            "Heartbeat direct RSS refresh failed, fallback to orchestrator. "
                            "user=%s err=%s",
                            user_id,
                            exc,
                        )

                if rss_refresh_available:
                    if rss_refresh_text:
                        self._append_delivery_section(
                            routed_sections,
                            spec_target,
                            rss_refresh_text,
                        )
                    if rss_refresh_text and not rss_refresh_appended:
                        sections.append(rss_refresh_text)
                        rss_refresh_appended = True
                    continue

            task_id = f"hb-{int(datetime.now().timestamp())}-{idx}"
            ctx.user_data["runtime_task_id"] = task_id
            ctx.user_data.pop("task_inbox_id", None)
            ctx.user_data.pop("pending_ui", None)
            ctx.user_data.pop("subagent_progress_steps", None)
            ctx.user_data.pop("subagent_progress_final_preview", None)
            ctx.user_data.pop("heartbeat_pending_files", None)
            prompt = self._build_heartbeat_task_prompt(
                task_id=task_id,
                goal=goal,
                readonly=self.mode == "readonly",
            )
            prepared_input = await build_agent_message_history(
                ctx,
                user_message=prompt,
                inline_input_source_texts=[goal],
                strip_refs_from_user_message=False,
            )
            if prepared_input.detected_refs and not prepared_input.has_inline_inputs:
                sections.append(
                    "❌ 检测到图片链接或本地图片路径，但没有成功加载任何图片。请检查链接或路径后重试。"
                )
                continue

            notice_parts: list[str] = []
            if prepared_input.truncated_inline_count:
                notice_parts.append(
                    f"⚠️ 检测到超过 {MAX_INLINE_IMAGE_INPUTS} 张图片，本次仅使用前 {MAX_INLINE_IMAGE_INPUTS} 张。"
                )
            if prepared_input.errors and prepared_input.has_inline_inputs:
                notice_parts.append(
                    f"⚠️ 有 {len(prepared_input.errors)} 张图片加载失败，先按成功加载的图片继续分析。"
                )
            if notice_parts:
                sections.append("\n".join(notice_parts))

            message_history = list(prepared_input.message_history)

            chunks: list[str] = []
            pending_spec_files: list[dict[str, str]] = []

            async def _ikaros_progress_callback(snapshot: dict[str, Any]) -> None:
                payload = dict(snapshot or {})
                if str(payload.get("event") or "").strip().lower() != "tool_call_finished":
                    return
                terminal_payload = payload.get("terminal_payload")
                if not isinstance(terminal_payload, dict):
                    return
                pending_spec_files[:] = merge_file_rows(
                    pending_spec_files,
                    normalize_file_rows(terminal_payload.get("files")),
                    extract_saved_file_rows(
                        str(terminal_payload.get("text") or "").strip()
                    ),
                )

            set_runtime_callback(ctx, "ikaros_progress_callback", _ikaros_progress_callback)
            try:
                stream = self._create_orchestrator_stream(ctx, message_history)
                async for chunk in stream:
                    if chunk:
                        chunks.append(str(chunk))
                    await heartbeat_store.refresh_lock(user_id, owner=owner)
            finally:
                pop_runtime_callback(ctx, "ikaros_progress_callback")

            stream_text = "\n".join(chunks).strip()

            def _quality_score(value: str) -> int:
                if not value:
                    return -1
                return (
                    len(value) + (value.count("http") * 200) + (value.count("\n") * 10)
                )

            text = stream_text
            if not text:
                text = "HEARTBEAT_OK"

            if "已派发给" in text and "完成后会自动把结果发给你" in text:
                text = "HEARTBEAT_OK"

            item_level, normalized_text = heartbeat_store.normalize_result_payload(text)
            pending_spec_files = merge_file_rows(
                pending_spec_files,
                normalize_file_rows(ctx.user_data.pop("heartbeat_pending_files", [])),
                extract_saved_file_rows(normalized_text or text),
            )
            if pending_spec_files:
                logger.info(
                    "Heartbeat collected delivery files user=%s task=%s target=%s/%s count=%s",
                    user_id,
                    task_id,
                    spec_target.get("platform", ""),
                    spec_target.get("chat_id", ""),
                    len(pending_spec_files),
                )
                self._append_delivery_files(
                    routed_files,
                    spec_target,
                    pending_spec_files,
                )

            if item_level == "OK" and not normalized_text:
                continue

            if level_rank.get(item_level, 0) > level_rank.get(overall_level, 0):
                overall_level = item_level

            rendered_text = normalized_text or text.strip()
            sections.append(rendered_text)
            self._append_delivery_section(routed_sections, spec_target, rendered_text)

        delivery_keys = list(
            dict.fromkeys([*routed_sections.keys(), *routed_files.keys()])
        )
        if not sections and not delivery_keys:
            return {"text": "HEARTBEAT_OK", "deliveries": [], "level": "OK"}
        return {
            "text": "\n\n".join(sections),
            "level": overall_level,
            "deliveries": [
                {
                    "platform": platform,
                    "chat_id": chat_id,
                    "text": "\n\n".join(routed_sections.get((platform, chat_id), [])),
                    "files": routed_files.get((platform, chat_id), []),
                }
                for (platform, chat_id) in delivery_keys
                if (
                    platform
                    and chat_id
                    and (
                        routed_sections.get((platform, chat_id))
                        or routed_files.get((platform, chat_id))
                    )
                )
            ],
        }

    async def _build_heartbeat_task_specs(
        self,
        *,
        user_id: str,
        checklist: list[str],
        checklist_items: list[dict[str, Any]] | None = None,
        default_delivery_target: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        normalized_items: list[dict[str, Any]] = []
        if checklist_items:
            for item in checklist_items:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                normalized_items.append(
                    {
                        "text": text,
                        "delivery_target": self._normalize_delivery_target(
                            item.get("delivery_target")
                            if isinstance(item.get("delivery_target"), dict)
                            else default_delivery_target
                        ),
                    }
                )
        else:
            for item in checklist:
                text = str(item or "").strip()
                if not text:
                    continue
                normalized_items.append(
                    {
                        "text": text,
                        "delivery_target": self._normalize_delivery_target(
                            default_delivery_target
                        ),
                    }
                )

        if not normalized_items:
            normalized_items.append(
                {
                    "text": _DEFAULT_HEARTBEAT_GOAL,
                    "delivery_target": self._normalize_delivery_target(
                        default_delivery_target
                    ),
                }
            )

        normalized = [str(item.get("text") or "").strip() for item in normalized_items]
        merged_checklist_text = "\n".join(normalized).lower()
        has_rss_focus = any(
            token in merged_checklist_text for token in ("rss", "订阅", "feed")
        )
        has_stock_focus = any(
            token in merged_checklist_text
            for token in ("股票", "自选股", "行情", "stock", "quote")
        )
        for idx, item in enumerate(normalized_items, start=1):
            specs.append(
                {
                    "type": "checklist",
                    "title": f"Heartbeat 检查项 {idx}",
                    "goal": str(item.get("text") or "").strip(),
                    "delivery_target": self._normalize_delivery_target(
                        item.get("delivery_target")
                        if isinstance(item.get("delivery_target"), dict)
                        else default_delivery_target
                    ),
                }
            )

        numeric_user_id = 0
        with contextlib.suppress(Exception):
            numeric_user_id = int(str(user_id))

        rss_delivery_target = self._normalize_delivery_target(default_delivery_target)
        stock_delivery_target = self._normalize_delivery_target(default_delivery_target)
        with contextlib.suppress(Exception):
            from extension.skills.learned.rss_subscribe.scripts.store import (
                get_rss_delivery_target,
            )
            from extension.skills.learned.stock_watch.scripts.store import (
                get_stock_delivery_target,
            )

            rss_target = await get_rss_delivery_target(user_id)
            if rss_target:
                rss_delivery_target = self._normalize_delivery_target(rss_target)
            stock_target = await get_stock_delivery_target(user_id)
            if stock_target:
                stock_delivery_target = self._normalize_delivery_target(stock_target)

        if self.enable_rss_signal and numeric_user_id > 0 and not has_rss_focus:
            with contextlib.suppress(Exception):
                from extension.skills.learned.rss_subscribe.scripts.store import (
                    list_subscriptions,
                )

                subs = await list_subscriptions(numeric_user_id)
                if subs:
                    specs.append(
                        {
                            "type": "rss_signal",
                            "title": "RSS 更新检查",
                            "goal": "检查用户 RSS 订阅最新更新。优先调用现有工具并直接交付工具结果，可在末尾补充简短观察。",
                            "delivery_target": rss_delivery_target,
                        }
                    )

        if (
            self.enable_stock_signal
            and numeric_user_id > 0
            and not normalized
            and not has_stock_focus
        ):
            with contextlib.suppress(Exception):
                from extension.skills.registry import skill_registry as skill_loader
                from extension.skills.learned.stock_watch.scripts.store import (
                    get_user_watchlist,
                )

                stock_module = skill_loader.import_skill_module("stock_watch")
                is_trading_time = getattr(stock_module, "is_trading_time", None)
                if callable(is_trading_time) and is_trading_time():
                    watchlist = await get_user_watchlist(numeric_user_id)
                    if watchlist:
                        specs.append(
                            {
                                "type": "stock_signal",
                                "title": "股票行情检查",
                                "goal": "获取用户自选股的最新行情并给出重点波动提醒。",
                                "delivery_target": stock_delivery_target,
                            }
                        )

        return specs

    @staticmethod
    def _build_heartbeat_task_prompt(*, task_id: str, goal: str, readonly: bool) -> str:
        readonly_line = (
            "当前为 readonly 模式：仅允许检查、查询、总结；允许执行用于订阅去重状态的轻量写入。"
            if readonly
            else "当前为 execute 模式：可以自行执行，或在需要并发/隔离时启动内部 subagent。"
        )
        return (
            "你正在处理 heartbeat 来源的任务项。\n"
            f"task_id: {task_id}\n"
            "source: heartbeat\n"
            f"goal: {goal}\n"
            f"{readonly_line}\n"
            "请自行决定：直接执行、调用扩展、或在必要时启动受控 subagent。\n"
            "如果目标是在回顾未完成任务、继续闭环、检查待办或跟进外部结果，先调用 `task_tracker` 查看 open task，再决定要推进哪一个。\n"
            "如果需要了解某个未完成任务最近发生了什么，优先继续用 `task_tracker` 获取任务级事件，不要直接扫 `data/task_inbox/events.jsonl`。\n"
            "若任务涉及外部事实查询（订阅更新/行情/检索/状态），请先调用至少一个可用工具，再基于工具结果作答。\n"
            "若工具返回可直接交付的结果（尤其链接/列表/命令/数字），请先完整保留工具原文，不要改写或删减。\n"
            "你可以在工具原文后追加『补充观察』，但必须与原文分段。\n"
            "最终输出必须采用以下格式之一：\n"
            "- `HEARTBEAT_OK`\n"
            "- `HEARTBEAT_NOTICE: <可直接推送给用户的自然语言结果>`\n"
            "- `HEARTBEAT_ACTION: <需要用户处理或尽快关注的自然语言结果>`\n"
            "不要输出其他系统模板前缀。"
        )

    @staticmethod
    def _split_push_chunks(text: str, limit: int = 3500) -> list[str]:
        return split_background_chunks(text, limit=limit)

    @staticmethod
    def _build_headless_context(user_id: str) -> UnifiedContext:
        user = User(
            id=str(user_id),
            username=f"hb_{user_id}",
            first_name="Heartbeat",
            last_name="Daemon",
        )
        chat = Chat(
            id=str(user_id),
            type="private",
            title="heartbeat_daemon",
        )
        message = UnifiedMessage(
            id=f"heartbeat-{int(datetime.now().timestamp())}",
            platform="heartbeat_daemon",
            user=user,
            chat=chat,
            date=datetime.now(),
            type=MessageType.TEXT,
            text="heartbeat",
        )
        adapter = _HeartbeatSilentAdapter()
        return UnifiedContext(
            message=message,
            platform_ctx=None,
            platform_event=None,
            _adapter=adapter,
            user=user,
        )

    async def _push_markdown_attachment(
        self,
        *,
        adapter: Any,
        platform: str,
        chat_id: str,
        text: str,
        user_id: str = "",
        session_id: str = "",
    ) -> bool:
        return await push_background_text(
            platform=platform,
            chat_id=chat_id,
            text=text,
            adapter=adapter,
            filename_prefix="heartbeat",
            file_enabled=True,
            file_threshold=1,
            max_text_chunks=0,
            record_history=bool(str(user_id or "").strip()),
            history_user_id=user_id,
            history_session_id=session_id,
        )

    async def _push_to_target(
        self,
        platform: str,
        chat_id: str,
        text: str,
        *,
        user_id: str = "",
        session_id: str = "",
    ) -> bool:
        try:
            return await push_background_text(
                platform=platform,
                chat_id=chat_id,
                text=text,
                filename_prefix="heartbeat",
                file_enabled=self.push_file_enabled,
                file_threshold=self.push_file_threshold,
                max_text_chunks=self.push_max_text_chunks,
                record_history=bool(str(user_id or "").strip()),
                history_user_id=user_id,
                history_session_id=session_id,
            )
        except Exception as exc:
            logger.error(
                "Heartbeat push failed: platform=%s chat=%s err=%s",
                platform,
                chat_id,
                exc,
            )
            return False

    async def _push_files_to_target(
        self,
        *,
        platform: str,
        chat_id: str,
        files: list[dict[str, str]],
    ) -> bool:
        safe_files = normalize_file_rows(files)
        if not safe_files:
            logger.info(
                "Heartbeat file delivery skipped: no normalized files. platform=%s chat=%s",
                platform,
                chat_id,
            )
            return False
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            logger.warning(
                "Heartbeat file delivery skipped: adapter unavailable. platform=%s chat=%s",
                platform,
                chat_id,
            )
            return False

        logger.info(
            "Heartbeat pushing %s file(s). platform=%s chat=%s",
            len(safe_files),
            platform,
            chat_id,
        )
        delivered = False
        for item in safe_files:
            path_text = str(item.get("path") or "").strip()
            if not path_text:
                continue
            path_obj = Path(path_text).expanduser().resolve()
            if not path_obj.exists() or not path_obj.is_file():
                continue
            kind = str(item.get("kind") or "document").strip().lower() or "document"
            caption = str(item.get("caption") or "").strip() or None
            filename = (
                str(item.get("filename") or path_obj.name).strip() or path_obj.name
            )
            logger.info(
                "Heartbeat file delivery attempt platform=%s chat=%s kind=%s path=%s filename=%s",
                platform,
                chat_id,
                kind,
                str(path_obj),
                filename,
            )
            sender = None
            kwargs: dict[str, Any] = {"chat_id": chat_id}
            if kind == "photo":
                sender = getattr(adapter, "send_photo", None)
                kwargs["photo"] = str(path_obj)
            elif kind == "video":
                sender = getattr(adapter, "send_video", None)
                kwargs["video"] = str(path_obj)
            elif kind == "audio":
                sender = getattr(adapter, "send_audio", None)
                kwargs["audio"] = str(path_obj)
            if not callable(sender):
                sender = getattr(adapter, "send_document", None)
                document: str | bytes = str(path_obj)
                output_name = filename
                if filename.lower().endswith(".md"):
                    try:
                        from services.md_converter import adapt_md_file_for_platform

                        document, output_name = adapt_md_file_for_platform(
                            file_bytes=path_obj.read_bytes(),
                            filename=filename,
                            platform=platform,
                        )
                    except Exception:
                        document = str(path_obj)
                kwargs = {
                    "chat_id": chat_id,
                    "document": document,
                    "filename": output_name,
                }
            if not callable(sender):
                continue
            if caption:
                kwargs["caption"] = caption
            try:
                result_obj = sender(**kwargs)
                if inspect.isawaitable(result_obj):
                    await result_obj
                delivered = True
            except Exception as exc:
                logger.error(
                    "Heartbeat file delivery failed. platform=%s chat=%s kind=%s path=%s err=%s",
                    platform,
                    chat_id,
                    kind,
                    str(path_obj),
                    exc,
                )
        return delivered

    @staticmethod
    def _create_orchestrator_stream(ctx: UnifiedContext, message_history: list):
        """
        Compatibility wrapper for monkeypatched/instance-bound orchestrator handlers in tests.
        """
        handler = getattr(agent_orchestrator, "handle_message", None)
        if handler is None:
            raise RuntimeError("agent_orchestrator.handle_message is not available")
        try:
            stream = handler(ctx, message_history)
        except TypeError as exc:
            func = getattr(handler, "__func__", None)
            if func and "positional arguments" in str(exc):
                stream = func(ctx, message_history)
            else:
                raise
        if not hasattr(stream, "__aiter__") and inspect.isawaitable(stream):

            async def _single():
                result = await stream
                if result is not None:
                    yield str(result)

            return _single()
        if not hasattr(stream, "__aiter__"):
            raise TypeError("orchestrator handle_message must return an async iterator")
        return stream


heartbeat_worker = HeartbeatWorker()
