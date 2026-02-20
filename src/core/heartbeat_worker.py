import asyncio
import contextlib
import io
import inspect
import logging
import os
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from core.agent_orchestrator import agent_orchestrator
from core.heartbeat_store import heartbeat_store
from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from core.platform.registry import adapter_manager
from core.task_manager import task_manager

logger = logging.getLogger(__name__)


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
        self.mode = (
            os.getenv("HEARTBEAT_MODE", "readonly").strip().lower() or "readonly"
        )
        self.readonly_dispatch = (
            os.getenv("HEARTBEAT_READONLY_DISPATCH", "false").lower() == "true"
        )
        self.enable_rss_signal = (
            os.getenv("HEARTBEAT_RSS_SIGNAL_ENABLED", "true").lower() == "true"
        )
        self.enable_stock_signal = (
            os.getenv("HEARTBEAT_STOCK_SIGNAL_ENABLED", "true").lower() == "true"
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
            delivery_target = await heartbeat_store.get_delivery_target(user_id)
            final_text = await self._run_heartbeat_task_batch(
                user_id=user_id,
                checklist=checklist,
                owner=owner,
                delivery_target=delivery_target,
            )

            if "已派发给" in final_text and "完成后会自动把结果发给你" in final_text:
                final_text = "HEARTBEAT_OK"

            level = heartbeat_store.classify_result(final_text)

            heartbeat_meta = await heartbeat_store.mark_heartbeat_run(
                user_id, final_text
            )
            with contextlib.suppress(Exception):
                from core.markdown_memory_store import markdown_memory_store

                rollup_result = await markdown_memory_store.rollup_today_sessions(
                    user_id
                )
                logger.info(
                    "Heartbeat daily rollup user=%s result=%s", user_id, rollup_result
                )
            await heartbeat_store.clear_last_error(user_id)

            if final_text.strip() == "HEARTBEAT_OK" and self.suppress_ok:
                return "HEARTBEAT_OK"

            if suppress_push:
                return final_text

            target = delivery_target
            platform = target.get("platform", "").strip()
            chat_id = target.get("chat_id", "").strip()
            if not platform or not chat_id:
                logger.info(
                    "Heartbeat result skipped push: no delivery target for user=%s",
                    user_id,
                )
                return final_text

            level = str(heartbeat_meta.get("last_level", level)).upper()
            text_to_push = final_text
            pushed = await self._push_to_target(
                platform=platform, chat_id=chat_id, text=text_to_push
            )
            if not pushed:
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
            await heartbeat_store.mark_heartbeat_run(user_id, f"ERROR: {exc}")
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
    ) -> str:
        specs = await self._build_heartbeat_task_specs(
            user_id=user_id, checklist=checklist
        )
        if not specs:
            return "HEARTBEAT_OK"

        ctx = self._build_headless_context(user_id)
        target_platform = str((delivery_target or {}).get("platform") or "").strip()
        target_chat_id = str((delivery_target or {}).get("chat_id") or "").strip()
        if target_platform:
            ctx.user_data["worker_delivery_platform"] = target_platform
        if target_chat_id:
            ctx.user_data["worker_delivery_chat_id"] = target_chat_id
        if self.mode == "readonly":
            ctx.user_data["execution_policy"] = "heartbeat_readonly_policy"
        else:
            ctx.user_data["execution_policy"] = "worker_execution_policy"

        sections: list[str] = []
        rss_refresh_attempted = False
        rss_refresh_available = False
        rss_refresh_text = ""
        rss_refresh_appended = False
        for idx, spec in enumerate(specs, start=1):
            await heartbeat_store.refresh_lock(user_id, owner=owner)
            title = str(spec.get("title") or f"任务 {idx}")
            goal = str(spec.get("goal") or "").strip()
            if not goal:
                continue

            spec_type = str(spec.get("type") or "").strip().lower()
            if spec_type == "rss_signal" or self._is_rss_related_goal(goal):
                if not rss_refresh_attempted:
                    rss_refresh_attempted = True
                    try:
                        from core.scheduler import trigger_manual_rss_check

                        rss_refresh_text = str(
                            await trigger_manual_rss_check(user_id) or ""
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
                    if rss_refresh_text and not rss_refresh_appended:
                        sections.append(rss_refresh_text)
                        rss_refresh_appended = True
                    continue

            task_id = f"hb-{int(datetime.now().timestamp())}-{idx}"
            prompt = self._build_heartbeat_task_prompt(
                task_id=task_id,
                goal=goal,
                readonly=self.mode == "readonly",
            )
            message_history = [{"role": "user", "parts": [{"text": prompt}]}]

            chunks: list[str] = []
            stream = self._create_orchestrator_stream(ctx, message_history)
            async for chunk in stream:
                if chunk:
                    chunks.append(str(chunk))
                await heartbeat_store.refresh_lock(user_id, owner=owner)

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

            if text.strip() == "HEARTBEAT_OK":
                continue

            sections.append(text.strip())

        if not sections:
            return "HEARTBEAT_OK"
        return "\n\n".join(sections)

    async def _build_heartbeat_task_specs(
        self,
        *,
        user_id: str,
        checklist: list[str],
    ) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        normalized = [
            str(item or "").strip() for item in checklist if str(item or "").strip()
        ]
        merged_checklist_text = "\n".join(normalized).lower()
        has_rss_focus = any(
            token in merged_checklist_text for token in ("rss", "订阅", "feed")
        )
        has_stock_focus = any(
            token in merged_checklist_text
            for token in ("股票", "自选股", "行情", "stock", "quote")
        )
        for idx, item in enumerate(normalized, start=1):
            specs.append(
                {
                    "type": "checklist",
                    "title": f"Heartbeat 检查项 {idx}",
                    "goal": item,
                }
            )

        numeric_user_id = 0
        with contextlib.suppress(Exception):
            numeric_user_id = int(str(user_id))

        if self.enable_rss_signal and numeric_user_id > 0 and not has_rss_focus:
            with contextlib.suppress(Exception):
                from core.state_store import get_user_subscriptions

                subs = await get_user_subscriptions(numeric_user_id)
                if subs:
                    specs.append(
                        {
                            "type": "rss_signal",
                            "title": "RSS 更新检查",
                            "goal": "检查用户 RSS 订阅最新更新。优先调用现有工具并直接交付工具结果，可在末尾补充简短观察。",
                        }
                    )

        if (
            self.enable_stock_signal
            and numeric_user_id > 0
            and not normalized
            and not has_stock_focus
        ):
            with contextlib.suppress(Exception):
                from core.scheduler import is_trading_time
                from core.state_store import get_user_watchlist

                if is_trading_time():
                    watchlist = await get_user_watchlist(numeric_user_id)
                    if watchlist:
                        specs.append(
                            {
                                "type": "stock_signal",
                                "title": "股票行情检查",
                                "goal": "获取用户自选股的最新行情并给出重点波动提醒。",
                            }
                        )

        return specs

    @staticmethod
    def _is_rss_related_goal(goal: str) -> bool:
        text = str(goal or "").strip().lower()
        if not text:
            return False
        return any(token in text for token in ("rss", "订阅", "feed"))

    @staticmethod
    def _build_heartbeat_task_prompt(*, task_id: str, goal: str, readonly: bool) -> str:
        readonly_line = (
            "当前为 readonly 模式：仅允许检查、查询、总结；允许执行用于状态去重的轻量写入（如 RSS last_entry_hash）。"
            if readonly
            else "当前为 execute 模式：可以按需派发 worker 完成任务。"
        )
        return (
            "你正在处理 heartbeat 来源的任务项。\n"
            f"task_id: {task_id}\n"
            "source: heartbeat\n"
            f"goal: {goal}\n"
            f"{readonly_line}\n"
            "请自行决定：直接执行、调用扩展、或派发给合适 worker。\n"
            "若任务涉及外部事实查询（订阅更新/行情/检索/状态），请先调用至少一个可用工具，再基于工具结果作答。\n"
            "若工具返回可直接交付的结果（尤其链接/列表/命令/数字），请先完整保留工具原文，不要改写或删减。\n"
            "你可以在工具原文后追加『补充观察』，但必须与原文分段。\n"
            "最终输出必须是可直接推送给用户的自然语言结果，不要输出系统模板前缀或固定说明。"
            "若无事项只输出 HEARTBEAT_OK。"
        )

    @staticmethod
    def _split_push_chunks(text: str, limit: int = 3500) -> list[str]:
        raw = str(text or "").strip()
        if not raw:
            return []
        if len(raw) <= limit:
            return [raw]

        chunks: list[str] = []
        remaining = raw
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            cut = remaining.rfind("\n\n", 0, limit)
            if cut < int(limit * 0.6):
                cut = remaining.rfind("\n", 0, limit)
            if cut < int(limit * 0.4):
                cut = limit

            part = remaining[:cut].strip()
            if part:
                chunks.append(part)
            remaining = remaining[cut:].strip()

        return chunks

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
    ) -> bool:
        payload = str(text or "").strip()
        if not payload:
            return True

        filename = f"heartbeat-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        caption = "📝 内容较长，完整结果见附件。"
        document_bytes = payload.encode("utf-8")

        # Platform-adaptive format conversion
        try:
            from services.md_converter import adapt_md_file_for_platform

            document_bytes, filename = adapt_md_file_for_platform(
                file_bytes=document_bytes,
                filename=filename,
                platform=platform,
            )
        except Exception:
            pass

        try:
            send_document = getattr(adapter, "send_document", None)
            if callable(send_document):
                send_result = send_document(
                    chat_id=chat_id,
                    document=document_bytes,
                    filename=filename,
                    caption=caption,
                )
                if inspect.isawaitable(send_result):
                    await send_result
                return True

            bot = getattr(adapter, "bot", None)
            if platform == "telegram" and bot is not None:
                file_obj = io.BytesIO(document_bytes)
                file_obj.name = filename
                send_result = bot.send_document(
                    chat_id=chat_id,
                    document=file_obj,
                    caption=caption,
                )
                if inspect.isawaitable(send_result):
                    await send_result
                return True
        except Exception as exc:
            logger.warning(
                "Heartbeat attachment push failed, fallback to chunked text. "
                "platform=%s chat=%s err=%s",
                platform,
                chat_id,
                exc,
            )

        return False

    async def _push_to_target(self, platform: str, chat_id: str, text: str) -> bool:
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            return False

        try:
            payload_text = str(text or "").strip()
            chunks = self._split_push_chunks(payload_text)
            if not chunks:
                return True

            if (
                self.push_file_enabled
                and (
                    len(chunks) > self.push_max_text_chunks
                    or len(payload_text) > self.push_file_threshold
                )
                and await self._push_markdown_attachment(
                    adapter=adapter,
                    platform=platform,
                    chat_id=chat_id,
                    text=payload_text,
                )
            ):
                return True

            total = len(chunks)
            for idx, chunk in enumerate(chunks, start=1):
                payload = chunk
                if total > 1:
                    payload = f"[{idx}/{total}]\n{chunk}"

                send_message = getattr(adapter, "send_message", None)
                if callable(send_message):
                    send_result = send_message(chat_id=chat_id, text=payload)
                    if inspect.isawaitable(send_result):
                        await send_result
                    continue

                bot = getattr(adapter, "bot", None)
                if platform == "telegram" and bot is not None:
                    html_payload = payload
                    with contextlib.suppress(Exception):
                        from platforms.telegram.formatter import (
                            markdown_to_telegram_html,
                        )

                        html_payload = markdown_to_telegram_html(payload)
                    send_result = bot.send_message(
                        chat_id=chat_id,
                        text=html_payload,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    if inspect.isawaitable(send_result):
                        await send_result
                    continue

                return False

            return True
        except Exception as exc:
            logger.error(
                "Heartbeat push failed: platform=%s chat=%s err=%s",
                platform,
                chat_id,
                exc,
            )
        return False

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
