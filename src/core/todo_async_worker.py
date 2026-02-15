import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional

from core.agent_orchestrator import agent_orchestrator
from core.config import DATA_DIR
from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from core.task_manager import task_manager

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_filename(name: str, default_name: str = "artifact.bin") -> str:
    raw = (name or "").strip()
    if not raw:
        return default_name
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)
    return safe or default_name


def _to_bytes(data) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    return b""


@dataclass
class PendingTodoTask:
    todo_path: Path
    task_dir: Path
    user_id: str
    task_id: str
    goal: str
    deliver_status: str
    content_hash: str


class _TodoHeadlessAdapter:
    def __init__(self, task: PendingTodoTask, event_writer):
        self._task = task
        self._event_writer = event_writer

    async def reply_text(self, ctx: UnifiedContext, text: str, ui=None, **kwargs):
        preview = str(text or "").replace("\n", " ")[:220]
        if preview:
            self._event_writer(self._task.todo_path, f"daemon_reply: {preview}")
        return SimpleNamespace(id=f"reply-{int(datetime.now().timestamp())}")

    async def edit_text(self, ctx: UnifiedContext, message_id: str, text: str, **kwargs):
        return await self.reply_text(ctx, text, **kwargs)

    async def reply_document(
        self,
        ctx: UnifiedContext,
        document,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        **kwargs,
    ):
        payload = _to_bytes(document)
        if not payload:
            return None
        out_dir = self._task.task_dir / "daemon_artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_filename(filename or "", default_name="artifact.bin")
        out_path = out_dir / name
        out_path.write_bytes(payload)
        self._event_writer(
            self._task.todo_path,
            f"daemon_artifact: {name} ({len(payload)} bytes)",
        )
        if caption:
            self._event_writer(self._task.todo_path, f"daemon_artifact_caption: {caption[:160]}")
        return SimpleNamespace(id=name)

    async def reply_photo(self, ctx: UnifiedContext, photo, caption: Optional[str] = None, **kwargs):
        return await self.reply_document(ctx, photo, filename="photo.bin", caption=caption, **kwargs)

    async def reply_video(self, ctx: UnifiedContext, video, caption: Optional[str] = None, **kwargs):
        return await self.reply_document(ctx, video, filename="video.bin", caption=caption, **kwargs)

    async def reply_audio(self, ctx: UnifiedContext, audio, caption: Optional[str] = None, **kwargs):
        return await self.reply_document(ctx, audio, filename="audio.bin", caption=caption, **kwargs)

    async def delete_message(self, ctx: UnifiedContext, message_id: str, chat_id: Optional[str] = None, **kwargs):
        return True

    async def send_chat_action(self, ctx: UnifiedContext, action: str, chat_id: Optional[str] = None, **kwargs):
        return True

    async def download_file(self, ctx: UnifiedContext, file_id: str, **kwargs) -> bytes:
        raise RuntimeError("todo_daemon context does not support file download")


class TodoAsyncWorker:
    def __init__(self):
        self.enabled = os.getenv("TODO_ASYNC_WORKER_ENABLED", "true").lower() == "true"
        self.poll_interval_sec = max(1, int(os.getenv("TODO_ASYNC_POLL_INTERVAL_SEC", "5")))
        self.stale_heartbeat_sec = max(0, int(os.getenv("TODO_ASYNC_STALE_HEARTBEAT_SEC", "20")))
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._running: Dict[str, asyncio.Task] = {}
        self._processed_hash: Dict[str, str] = {}

    async def start(self) -> None:
        if not self.enabled:
            logger.info("TODO async worker disabled by env.")
            return
        if self._loop_task and not self._loop_task.done():
            return
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._run_loop(), name="todo-async-worker")
        task_root = (Path(DATA_DIR) / "runtime_tasks").resolve()
        logger.info(
            "TODO async worker started. root=%s poll=%ss stale_heartbeat=%ss",
            task_root,
            self.poll_interval_sec,
            self.stale_heartbeat_sec,
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
                logger.error("TODO async worker loop error: %s", exc, exc_info=True)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_sec)
            except asyncio.TimeoutError:
                continue

    async def process_once(self) -> None:
        if not self.enabled:
            return
        root = Path(DATA_DIR) / "runtime_tasks"
        if not root.exists():
            return

        for todo_path in root.glob("*/*/TODO.md"):
            pending = self._parse_pending_task(todo_path)
            if not pending:
                continue

            key = str(todo_path.resolve())
            if key in self._running:
                continue
            if task_manager.has_active_task(pending.user_id):
                continue
            if self._is_heartbeat_fresh(pending.task_dir):
                continue

            if self._processed_hash.get(key) == pending.content_hash:
                continue

            self._processed_hash[key] = pending.content_hash
            run_task = asyncio.create_task(
                self._execute_pending_task(pending),
                name=f"todo-runner-{pending.user_id}-{pending.task_id}",
            )
            self._running[key] = run_task
            run_task.add_done_callback(lambda _t, task_key=key: self._running.pop(task_key, None))

    def _parse_pending_task(self, todo_path: Path) -> Optional[PendingTodoTask]:
        try:
            text = todo_path.read_text(encoding="utf-8")
        except Exception:
            return None

        if not text.strip():
            return None

        deliver_status = self._extract_step_status(text, "deliver")
        if deliver_status == "done":
            return None

        goal = self._extract_goal(text)
        if not goal:
            return None

        user_id_match = re.search(r"^- User:\s*`([^`]+)`", text, flags=re.MULTILINE)
        task_id_match = re.search(r"^- Task ID:\s*`([^`]+)`", text, flags=re.MULTILINE)
        user_id = user_id_match.group(1).strip() if user_id_match else todo_path.parent.parent.name
        task_id = task_id_match.group(1).strip() if task_id_match else todo_path.parent.name

        content_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
        return PendingTodoTask(
            todo_path=todo_path.resolve(),
            task_dir=todo_path.parent.resolve(),
            user_id=str(user_id),
            task_id=str(task_id),
            goal=goal,
            deliver_status=deliver_status,
            content_hash=content_hash,
        )

    def _extract_goal(self, text: str) -> str:
        match = re.search(r"## Goal\s*\n(?P<body>(?:>.*\n?)*)", text, flags=re.IGNORECASE)
        if match:
            lines = []
            for raw in match.group("body").splitlines():
                raw = raw.strip()
                if raw.startswith(">"):
                    lines.append(raw.lstrip(">").strip())
            goal = "\n".join([line for line in lines if line]).strip()
            if goal:
                return goal

        first_meaningful = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#") or line.startswith("- Task ID:") or line.startswith("- User:"):
                continue
            if line.startswith("- ["):
                continue
            first_meaningful = line
            break
        return first_meaningful

    def _extract_step_status(self, text: str, step_key: str) -> str:
        match = re.search(
            rf"- \[[ x]\]\s*`{re.escape(step_key)}`[^\n]*\(([^,\)\n]+)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return "pending"
        return match.group(1).strip().lower()

    def _is_heartbeat_fresh(self, task_dir: Path) -> bool:
        heartbeat_path = task_dir / "heartbeat.json"
        if not heartbeat_path.exists():
            return False
        try:
            data = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            updated_at = str(data.get("updated_at", "")).strip()
            if not updated_at:
                return False
            dt = datetime.fromisoformat(updated_at)
        except Exception:
            return False
        age = (datetime.now() - dt).total_seconds()
        return age < self.stale_heartbeat_sec

    async def _execute_pending_task(self, pending: PendingTodoTask) -> None:
        self._append_todo_event(pending.todo_path, "daemon_dispatch")
        self._write_heartbeat(pending, "daemon:dispatched")
        logger.info(
            "TODO daemon executing: user=%s task=%s goal=%s",
            pending.user_id,
            pending.task_id,
            pending.goal[:120],
        )

        ctx = self._build_headless_context(pending)
        current = asyncio.current_task()
        await task_manager.register_task(
            pending.user_id,
            current,
            description="TODO 异步执行",
            todo_path=str(pending.todo_path),
            task_id=pending.task_id,
        )

        chunks: list[str] = []
        try:
            message_history = [{"role": "user", "parts": [{"text": pending.goal}]}]
            async for chunk in agent_orchestrator.handle_message(ctx, message_history):
                if chunk:
                    chunks.append(str(chunk))

            final_text = "\n".join(chunks).strip()
            if final_text:
                self._write_result(pending, final_text)
                self._append_todo_event(
                    pending.todo_path,
                    f"daemon_result: {final_text.replace(chr(10), ' ')[:220]}",
                )
            else:
                self._append_todo_event(pending.todo_path, "daemon_result: (empty)")

            self._write_heartbeat(pending, "daemon:completed")
        except asyncio.CancelledError:
            self._append_todo_event(pending.todo_path, "daemon_cancelled")
            self._write_heartbeat(pending, "daemon:cancelled")
            raise
        except Exception as exc:
            logger.error("TODO daemon execute error: %s", exc, exc_info=True)
            self._append_todo_event(pending.todo_path, f"daemon_error: {exc}")
            self._write_heartbeat(pending, f"daemon:error:{exc}")
        finally:
            task_manager.unregister_task(pending.user_id)
            self._processed_hash[str(pending.todo_path.resolve())] = self._hash_file(pending.todo_path)

    def _build_headless_context(self, pending: PendingTodoTask) -> UnifiedContext:
        user = User(
            id=str(pending.user_id),
            username=f"todo_{pending.user_id}",
            first_name="TODO",
            last_name="Daemon",
        )
        chat = Chat(
            id=str(pending.user_id),
            type="private",
            title="todo_daemon",
        )
        message = UnifiedMessage(
            id=f"todo-{pending.task_id}",
            platform="todo_daemon",
            user=user,
            chat=chat,
            date=datetime.now(),
            type=MessageType.TEXT,
            text=pending.goal,
        )
        adapter = _TodoHeadlessAdapter(pending, self._append_todo_event)
        return UnifiedContext(
            message=message,
            platform_ctx=None,
            platform_event=None,
            _adapter=adapter,
            user=user,
        )

    def _append_todo_event(self, todo_path: Path, message: str) -> None:
        note = f"{_now_iso()} | {message}"
        try:
            text = todo_path.read_text(encoding="utf-8")
        except Exception:
            return

        if "## Recent Events" in text:
            if not text.endswith("\n"):
                text += "\n"
            text += f"- {note}\n"
        else:
            if not text.endswith("\n"):
                text += "\n"
            text += f"\n## Recent Events\n- {note}\n"

        try:
            todo_path.write_text(text, encoding="utf-8")
        except Exception:
            return

    def _write_heartbeat(self, pending: PendingTodoTask, note: str) -> None:
        path = pending.task_dir / "heartbeat.json"
        data = {
            "task_id": pending.task_id,
            "user_id": pending.user_id,
            "updated_at": _now_iso(),
            "events": [],
        }
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data.update(raw)
        except Exception:
            pass

        events = list(data.get("events") or [])
        events.append(f"{_now_iso()} | {note}")
        data["events"] = events[-20:]
        data["updated_at"] = _now_iso()
        data["task_id"] = pending.task_id
        data["user_id"] = pending.user_id

        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return

    def _write_result(self, pending: PendingTodoTask, content: str) -> None:
        path = pending.task_dir / "RESULT.md"
        block = (
            f"\n## {_now_iso()}\n\n"
            f"{content.strip()}\n"
        )
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except Exception:
                existing = ""
            text = existing + block
        else:
            text = f"# RESULT\n{block}"
        path.write_text(text, encoding="utf-8")

    def _hash_file(self, path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return ""
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

todo_async_worker = TodoAsyncWorker()
