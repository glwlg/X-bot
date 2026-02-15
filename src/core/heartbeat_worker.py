import asyncio
import contextlib
import inspect
import logging
import os
from datetime import datetime
from types import SimpleNamespace

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

    async def edit_text(self, ctx: UnifiedContext, message_id: str, text: str, **kwargs):
        return SimpleNamespace(id=message_id)

    async def reply_document(self, ctx: UnifiedContext, document, filename=None, caption=None, **kwargs):
        return SimpleNamespace(id=filename or "doc")

    async def reply_photo(self, ctx: UnifiedContext, photo, caption=None, **kwargs):
        return SimpleNamespace(id="photo")

    async def reply_video(self, ctx: UnifiedContext, video, caption=None, **kwargs):
        return SimpleNamespace(id="video")

    async def reply_audio(self, ctx: UnifiedContext, audio, caption=None, **kwargs):
        return SimpleNamespace(id="audio")

    async def delete_message(self, ctx: UnifiedContext, message_id: str, chat_id=None, **kwargs):
        return True

    async def send_chat_action(self, ctx: UnifiedContext, action: str, chat_id=None, **kwargs):
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
        self.mode = (os.getenv("HEARTBEAT_MODE", "readonly").strip().lower() or "readonly")
        self.readonly_dispatch = os.getenv("HEARTBEAT_READONLY_DISPATCH", "false").lower() == "true"
        self.enable_rss_signal = os.getenv("HEARTBEAT_RSS_SIGNAL_ENABLED", "true").lower() == "true"
        self.enable_stock_signal = os.getenv("HEARTBEAT_STOCK_SIGNAL_ENABLED", "true").lower() == "true"
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
            logger.info("Heartbeat store compacted for %s user(s) on startup.", compacted)
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._run_loop(), name="heartbeat-worker-loop")
        logger.info("Heartbeat worker started. root=%s tick=%ss", heartbeat_store.root, self.tick_sec)

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

    async def run_user_now(self, user_id: str) -> str:
        """Manual trigger for /heartbeat run."""
        user_id = str(user_id)
        if user_id in self._running:
            return "Heartbeat already running."
        return await self._run_heartbeat_for_user(user_id, force=True)

    async def _run_heartbeat_for_user(self, user_id: str, force: bool) -> str:
        user_id = str(user_id)
        owner = f"hb:{user_id}:{int(datetime.now().timestamp())}"
        locked = await heartbeat_store.claim_lock(user_id, owner=owner)
        if not locked:
            return "lock_busy"

        current = asyncio.current_task()
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
            prompt = self._build_heartbeat_prompt(checklist)
            ctx = self._build_headless_context(user_id)
            ctx.user_data["execution_policy"] = "heartbeat_readonly_policy"
            message_history = [{"role": "user", "parts": [{"text": prompt}]}]

            chunks: list[str] = []
            stream = self._create_orchestrator_stream(ctx, message_history)
            async for chunk in stream:
                if chunk:
                    chunks.append(str(chunk))
                await heartbeat_store.refresh_lock(user_id, owner=owner)

            final_text = "\n".join(chunks).strip() or "HEARTBEAT_OK"
            builtin_signals = await self._collect_builtin_skill_signals(user_id)
            if builtin_signals:
                if final_text.strip() == "HEARTBEAT_OK":
                    final_text = builtin_signals
                else:
                    final_text = f"{final_text}\n\n{builtin_signals}"

            level = heartbeat_store.classify_result(final_text)

            if (
                self.mode == "readonly"
                and level == "ACTION"
            ):
                if self.readonly_dispatch:
                    logger.warning(
                        "HEARTBEAT_READONLY_DISPATCH is enabled but ignored: "
                        "heartbeat readonly mode no longer dispatches repair tasks to workers."
                    )
                final_text = self._format_readonly_action_findings(final_text)
                level = heartbeat_store.classify_result(final_text)

            heartbeat_meta = await heartbeat_store.mark_heartbeat_run(user_id, final_text)
            await heartbeat_store.clear_last_error(user_id)

            if final_text.strip() == "HEARTBEAT_OK" and self.suppress_ok:
                return "HEARTBEAT_OK"

            target = await heartbeat_store.get_delivery_target(user_id)
            platform = target.get("platform", "").strip()
            chat_id = target.get("chat_id", "").strip()
            if not platform or not chat_id:
                logger.info("Heartbeat result skipped push: no delivery target for user=%s", user_id)
                return final_text

            level = str(heartbeat_meta.get("last_level", level)).upper()
            text_to_push = final_text if level == "OK" else f"[{level}] {final_text}"
            pushed = await self._push_to_target(platform=platform, chat_id=chat_id, text=text_to_push)
            if not pushed:
                logger.warning("Heartbeat result push failed. user=%s platform=%s chat=%s", user_id, platform, chat_id)
            return final_text
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Heartbeat user run error: user=%s err=%s", user_id, exc, exc_info=True)
            await heartbeat_store.set_last_error(user_id, str(exc))
            await heartbeat_store.mark_heartbeat_run(user_id, f"ERROR: {exc}")
            return f"ERROR: {exc}"
        finally:
            await heartbeat_store.release_lock(user_id, owner=owner)
            task_manager.unregister_task(user_id)

    @staticmethod
    def _build_heartbeat_prompt(checklist: list[str]) -> str:
        lines = [f"- {item}" for item in checklist if str(item or "").strip()]
        body = "\n".join(lines) if lines else "- Check important updates and only report action-required items."
        return (
            "你正在执行周期心跳检查。\n"
            "严格按以下 checklist 逐项检查。\n"
            "如果没有任何需要用户采取行动的事项，只输出：HEARTBEAT_OK\n"
            "如果有事项，输出精炼摘要（包含行动建议）。\n\n"
            "# Heartbeat checklist\n"
            f"{body}"
        )

    async def _collect_builtin_skill_signals(self, user_id: str) -> str:
        sections: list[str] = []

        if self.enable_rss_signal:
            try:
                from core.scheduler import trigger_manual_rss_check

                rss_text = str(await trigger_manual_rss_check(user_id) or "").strip()
                if rss_text:
                    sections.append(
                        "RSS 信号（heartbeat 驱动，无内置定时器）:\n"
                        + self._truncate_signal(rss_text, 1400)
                    )
            except Exception as exc:
                logger.debug("Heartbeat RSS signal check failed: %s", exc, exc_info=True)

        if self.enable_stock_signal:
            try:
                from core.scheduler import trigger_manual_stock_check, is_trading_time

                if is_trading_time():
                    stock_text = str(await trigger_manual_stock_check(user_id) or "").strip()
                    if stock_text:
                        sections.append(
                            "股票信号（heartbeat 驱动，无内置定时器）:\n"
                            + self._truncate_signal(stock_text, 1000)
                        )
            except Exception as exc:
                logger.debug("Heartbeat stock signal check failed: %s", exc, exc_info=True)

        if not sections:
            return ""
        return "NOTICE: 心跳检测到以下信息更新（无需修复动作）：\n\n" + "\n\n".join(sections)

    @staticmethod
    def _truncate_signal(text: str, limit: int = 1200) -> str:
        raw = str(text or "").strip()
        if len(raw) <= limit:
            return raw
        return raw[:limit] + "\n...[truncated]"

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

    async def _push_to_target(self, platform: str, chat_id: str, text: str) -> bool:
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            return False

        try:
            if platform == "telegram" and hasattr(adapter, "bot"):
                await adapter.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
                return True

            if hasattr(adapter, "send_message"):
                await adapter.send_message(chat_id=chat_id, text=text)
                return True
        except Exception as exc:
            logger.error("Heartbeat push failed: platform=%s chat=%s err=%s", platform, chat_id, exc)
        return False

    @staticmethod
    def _format_readonly_action_findings(findings: str) -> str:
        return (
            "🫀 Heartbeat 检测到需要用户处理的维护事项。\n"
            "- 说明：该事项属于 Core Manager 治理提醒，不会派发 Worker 执行层修复任务。\n"
            "- 原因：当前为 heartbeat readonly 模式，禁止自动执行系统级变更。\n\n"
            f"检查发现：\n{str(findings or '').strip()}"
        )

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
