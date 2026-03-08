from __future__ import annotations

import asyncio
import inspect
import os
from datetime import datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict

from core.agent_orchestrator import agent_orchestrator
from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from core.runtime_callbacks import set_runtime_callback
from shared.contracts.dispatch import TaskEnvelope, TaskResult


def _worker_user_id(task: TaskEnvelope) -> str:
    metadata = dict(task.metadata or {})
    logical_user = (
        str(
            metadata.get("user_id") or metadata.get("chat_id") or task.worker_id
        ).strip()
        or task.worker_id
    )
    return f"worker::{task.worker_id}::{logical_user}"


def _extract_pending_ui(raw_pending_ui: Any) -> Dict[str, Any] | None:
    if isinstance(raw_pending_ui, dict):
        actions = raw_pending_ui.get("actions")
        if isinstance(actions, list) and actions:
            return {"actions": list(actions)}
        return None

    if not isinstance(raw_pending_ui, list):
        return None

    merged: list[Any] = []
    for block in raw_pending_ui:
        if not isinstance(block, dict):
            continue
        actions = block.get("actions")
        if isinstance(actions, list):
            merged.extend(actions)
    if not merged:
        return None
    return {"actions": merged}


def _coerce_stream(raw_stream: Any) -> AsyncIterator[str]:
    if hasattr(raw_stream, "__aiter__"):
        return raw_stream
    if inspect.isawaitable(raw_stream):

        async def _single() -> AsyncIterator[str]:
            result = await raw_stream
            if result is not None:
                yield str(result)

        return _single()
    raise TypeError("agent_orchestrator.handle_message must return an async iterator")


class _WorkerSilentAdapter:
    async def reply_text(self, ctx, text: str, ui=None, **kwargs):
        _ = (ctx, text, ui, kwargs)
        return SimpleNamespace(id=f"worker-silent-{int(datetime.now().timestamp())}")

    async def edit_text(self, ctx, message_id: str, text: str, **kwargs):
        _ = (ctx, text, kwargs)
        return SimpleNamespace(id=message_id)

    async def reply_document(
        self, ctx, document, filename=None, caption=None, **kwargs
    ):
        _ = (ctx, document, caption, kwargs)
        return SimpleNamespace(id=filename or "doc")

    async def reply_photo(self, ctx, photo, caption=None, **kwargs):
        _ = (ctx, photo, caption, kwargs)
        return SimpleNamespace(id="photo")

    async def reply_video(self, ctx, video, caption=None, **kwargs):
        _ = (ctx, video, caption, kwargs)
        return SimpleNamespace(id="video")

    async def reply_audio(self, ctx, audio, caption=None, **kwargs):
        _ = (ctx, audio, caption, kwargs)
        return SimpleNamespace(id="audio")

    async def delete_message(self, ctx, message_id: str, chat_id=None, **kwargs):
        _ = (ctx, message_id, chat_id, kwargs)
        return True

    async def send_chat_action(self, ctx, action: str, chat_id=None, **kwargs):
        _ = (ctx, action, chat_id, kwargs)
        return True

    async def download_file(self, ctx, file_id: str, **kwargs) -> bytes:
        _ = (ctx, file_id, kwargs)
        raise RuntimeError("worker context does not support file download")


def _build_context(task: TaskEnvelope) -> UnifiedContext:
    worker_user_id = _worker_user_id(task)
    logical_user = worker_user_id.split("::", 2)[-1].strip() or task.worker_id
    now = datetime.now()
    user = User(
        id=logical_user,
        username=f"worker_{task.worker_id}",
        first_name="Worker",
        last_name="Agent",
    )
    chat = Chat(
        id=logical_user,
        type="private",
        title=f"worker-{task.worker_id}",
    )
    message = UnifiedMessage(
        id=f"worker-msg-{int(now.timestamp())}",
        platform="worker_kernel",
        user=user,
        chat=chat,
        date=now,
        type=MessageType.TEXT,
        text=str(task.instruction or ""),
    )
    ctx = UnifiedContext(
        message=message,
        platform_ctx=None,
        platform_event=None,
        _adapter=_WorkerSilentAdapter(),
        user=user,
    )
    ctx.user_data["execution_policy"] = "worker_execution_policy"
    ctx.user_data["runtime_user_id"] = worker_user_id
    metadata = dict(task.metadata or {})
    session_id = str(metadata.get("session_id") or "").strip()
    if session_id:
        ctx.user_data["session_id"] = session_id
    return ctx


async def _collect_chunks(stream: AsyncIterator[str]) -> list[str]:
    chunks: list[str] = []
    async for chunk in stream:
        text = str(chunk or "").strip()
        if text:
            chunks.append(text)
    return chunks


async def run_core_agent(task: TaskEnvelope, context: Dict[str, Any]) -> TaskResult:
    from shared.queue.dispatch_queue import dispatch_queue

    ctx = _build_context(task)
    latest_terminal_payload: Dict[str, Any] = {}
    latest_terminal_text = ""
    latest_terminal_summary = ""

    async def _progress_callback(snapshot: Dict[str, Any]) -> None:
        nonlocal latest_terminal_payload, latest_terminal_text, latest_terminal_summary
        await dispatch_queue.update_progress(task.task_id, snapshot)
        event_name = str(snapshot.get("event") or "").strip().lower()
        if event_name != "tool_call_finished":
            return
        if not bool(snapshot.get("ok")) or not bool(snapshot.get("terminal")):
            return
        terminal_payload = snapshot.get("terminal_payload")
        if isinstance(terminal_payload, dict) and terminal_payload:
            latest_terminal_payload = dict(terminal_payload)
        latest_terminal_text = str(snapshot.get("terminal_text") or "").strip()
        latest_terminal_summary = str(snapshot.get("summary") or "").strip()

    set_runtime_callback(ctx, "worker_progress_callback", _progress_callback)
    history = [{"role": "user", "parts": [{"text": str(task.instruction or "")}]}]
    timeout_sec = max(30, int(os.getenv("WORKER_CORE_AGENT_TIMEOUT_SEC", "1200")))

    try:
        raw_stream = agent_orchestrator.handle_message(ctx, history)
        stream = _coerce_stream(raw_stream)
        chunks = await asyncio.wait_for(_collect_chunks(stream), timeout=timeout_sec)
        final_text = "\n".join(chunks).strip()
        payload: Dict[str, Any] = dict(latest_terminal_payload or {})
        payload_text = str(payload.get("text") or "").strip()
        if not final_text:
            final_text = payload_text or latest_terminal_text
        if not final_text:
            final_text = "worker core-agent finished with no text output"
        if not payload_text:
            payload["text"] = final_text
        ui_payload = _extract_pending_ui(ctx.user_data.get("pending_ui"))
        if ui_payload and not isinstance(payload.get("ui"), dict):
            payload["ui"] = ui_payload
        summary_text = (
            str(payload.get("text") or "").strip()
            or latest_terminal_summary
            or final_text
        )
        return TaskResult(
            task_id=task.task_id,
            worker_id=str(context.get("worker_id") or task.worker_id),
            ok=True,
            summary=summary_text[:200],
            payload=payload,
        )
    except asyncio.TimeoutError:
        message = f"worker core-agent timeout after {timeout_sec}s"
        return TaskResult(
            task_id=task.task_id,
            worker_id=str(context.get("worker_id") or task.worker_id),
            ok=False,
            summary=message,
            error=message,
            payload={"text": message},
        )
    except Exception as exc:
        message = f"worker core-agent failed: {exc}"
        return TaskResult(
            task_id=task.task_id,
            worker_id=str(context.get("worker_id") or task.worker_id),
            ok=False,
            summary=message,
            error=message,
            payload={"text": message},
        )


class Program:
    async def run(self, task: TaskEnvelope, context: Dict[str, Any]) -> TaskResult:
        return await run_core_agent(task, context)


def build_program() -> Program:
    return Program()
