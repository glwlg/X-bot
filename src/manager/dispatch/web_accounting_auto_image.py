from __future__ import annotations

import asyncio
import base64
import logging
import os
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from core.prompt_composer import prompt_composer
from core.skill_loader import skill_loader
from services.ai_service import AiService
from shared.contracts.dispatch import TaskEnvelope, TaskResult

logger = logging.getLogger(__name__)

_ai_service = AiService()


class _ManagerSilentAdapter:
    async def reply_text(self, ctx, text: str, ui=None, **kwargs):
        _ = (ctx, text, ui, kwargs)
        return SimpleNamespace(id="manager-web-accounting-reply")

    async def edit_text(self, ctx, message_id: str, text: str, **kwargs):
        _ = (ctx, message_id, text, kwargs)
        return SimpleNamespace(id=message_id)

    async def reply_document(
        self, ctx, document, filename=None, caption=None, **kwargs
    ):
        _ = (ctx, document, filename, caption, kwargs)
        return SimpleNamespace(id="manager-web-accounting-doc")

    async def reply_photo(self, ctx, photo, caption=None, **kwargs):
        _ = (ctx, photo, caption, kwargs)
        return SimpleNamespace(id="manager-web-accounting-photo")

    async def reply_video(self, ctx, video, caption=None, **kwargs):
        _ = (ctx, video, caption, kwargs)
        return SimpleNamespace(id="manager-web-accounting-video")

    async def reply_audio(self, ctx, audio, caption=None, **kwargs):
        _ = (ctx, audio, caption, kwargs)
        return SimpleNamespace(id="manager-web-accounting-audio")

    async def delete_message(self, ctx, message_id: str, chat_id=None, **kwargs):
        _ = (ctx, message_id, chat_id, kwargs)
        return True

    async def send_chat_action(self, ctx, action: str, chat_id=None, **kwargs):
        _ = (ctx, action, chat_id, kwargs)
        return True

    async def download_file(self, ctx, file_id: str, **kwargs):
        _ = (ctx, file_id, kwargs)
        raise RuntimeError(
            "manager web accounting context does not support file download"
        )


def _build_quick_accounting_tool_declaration(skill: dict) -> dict:
    schema = skill.get("input_schema") if isinstance(skill, dict) else None
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    description = str(skill.get("description") or "") if isinstance(skill, dict) else ""
    return {
        "name": "ext_quick_accounting",
        "description": (
            description + " 这是网页版图片记账场景，必须调用该工具完成真实入账。"
        ).strip(),
        "parameters": schema,
    }


def _load_image(task: TaskEnvelope) -> tuple[bytes, str]:
    metadata = dict(task.metadata or {})
    path_text = str(metadata.get("web_accounting_image_path") or "").strip()
    if not path_text:
        raise RuntimeError("missing web_accounting_image_path")

    data_dir = str(os.getenv("DATA_DIR", "/app/data")).strip() or "/app/data"
    allow_root = (
        Path(data_dir).expanduser().resolve() / "system" / "web_accounting_uploads"
    )
    image_path = Path(path_text).expanduser().resolve()
    if not str(image_path).startswith(str(allow_root)):
        raise RuntimeError("invalid image path")
    if not image_path.exists() or not image_path.is_file():
        raise RuntimeError("uploaded image not found")

    raw = image_path.read_bytes()
    if not raw:
        raise RuntimeError("uploaded image is empty")
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("uploaded image too large")

    mime_type = str(metadata.get("web_accounting_image_mime") or "").strip().lower()
    if not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    return raw, mime_type


def _build_ctx(task: TaskEnvelope) -> UnifiedContext:
    metadata = dict(task.metadata or {})
    user_id = int(metadata.get("accounting_user_id") or 0)
    if user_id <= 0:
        raise RuntimeError("missing accounting user id")
    book_id = int(metadata.get("accounting_book_id") or 0)
    if book_id <= 0:
        raise RuntimeError("missing accounting book id")

    now = datetime.now()
    runtime_user = User(
        id=str(user_id),
        username=f"web_user_{user_id}",
        first_name="WebUser",
    )
    runtime_chat = Chat(
        id=f"web-accounting-{user_id}",
        type="private",
        title="web-accounting",
    )
    runtime_message = UnifiedMessage(
        id=f"web-accounting-{int(now.timestamp())}",
        platform="web_accounting",
        user=runtime_user,
        chat=runtime_chat,
        date=now,
        type=MessageType.IMAGE,
        text=str(task.instruction or ""),
    )
    ctx = UnifiedContext(
        message=runtime_message,
        platform_ctx=SimpleNamespace(user_data={}),
        platform_event=None,
        _adapter=_ManagerSilentAdapter(),
        user=runtime_user,
    )
    ctx.user_data["runtime_user_id"] = f"web::{user_id}"
    ctx.user_data["accounting_user_id"] = user_id
    ctx.user_data["accounting_book_id"] = book_id
    ctx.user_data["accounting_source"] = str(
        metadata.get("accounting_source") or "web_clipboard"
    )
    return ctx


def _extract_record_and_book_id(
    data: dict[str, Any],
    *,
    fallback_book_id: Any,
) -> tuple[int, int]:
    payload = data.get("payload") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        record_id_raw = payload.get("record_id")
        book_id_raw = payload.get("book_id")
    else:
        record_id_raw = data.get("record_id")
        book_id_raw = data.get("book_id")

    try:
        record_id = int(record_id_raw) if record_id_raw is not None else 0
    except (TypeError, ValueError):
        record_id = 0

    try:
        book_id = int(book_id_raw) if book_id_raw is not None else 0
    except (TypeError, ValueError):
        try:
            book_id = int(fallback_book_id) if fallback_book_id is not None else 0
        except (TypeError, ValueError):
            book_id = 0
    return record_id, book_id


async def run_web_accounting_auto_image_task(task: TaskEnvelope) -> TaskResult:
    metadata = dict(task.metadata or {})

    skill = skill_loader.get_skill("quick_accounting")
    if not isinstance(skill, dict):
        return TaskResult(
            task_id=task.task_id,
            worker_id=task.worker_id,
            ok=False,
            summary="服务端未加载 quick_accounting 技能",
            error="service_skill_missing",
            payload={
                "text": "服务端未加载 quick_accounting 技能，请联系管理员检查部署。"
            },
        )

    image_bytes, mime_type = _load_image(task)
    tool_decl = _build_quick_accounting_tool_declaration(skill)
    ctx = _build_ctx(task)
    system_instruction = prompt_composer.compose_base(
        runtime_user_id=str(ctx.user_data.get("runtime_user_id") or ""),
        platform=str(getattr(ctx.message, "platform", "") or ""),
        tools=[tool_decl],
        runtime_policy_ctx={
            "agent_kind": "core-manager",
            "policy": {"tools": {"allow": ["tool:ext_quick_accounting"], "deny": []}},
        },
        mode="media_image",
    )

    called = 0
    last_error = ""
    final_text = ""
    captured_data: dict[str, Any] = {}
    stream_timed_out = False
    stream_error = ""
    stream_timeout_sec = max(
        20.0,
        float(os.getenv("WEB_ACCOUNTING_STREAM_TIMEOUT_SEC", "75")),
    )
    loop = asyncio.get_running_loop()
    success_future: asyncio.Future[dict[str, int]] = loop.create_future()

    async def tool_executor(name: str, args: dict[str, object]) -> dict:
        nonlocal called, last_error, captured_data
        if name != "ext_quick_accounting":
            return {
                "ok": False,
                "error_code": "tool_not_allowed",
                "message": f"Tool not allowed: {name}",
                "failure_mode": "recoverable",
            }
        called += 1
        return {
            "ok": False,
            "error_code": "deprecated_extension_executor",
            "message": "The extension executor has been removed. Please use LLM SOPs + primitive tools.",
            "failure_mode": "fatal",
        }

    message_history = [
        {
            "role": "user",
            "parts": [
                {"text": str(task.instruction or "")},
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(image_bytes).decode("utf-8"),
                    }
                },
            ],
        }
    ]

    async def consume_stream() -> None:
        nonlocal final_text
        async for chunk in _ai_service.generate_response_stream(
            message_history,
            tools=[tool_decl],
            tool_executor=tool_executor,
            system_instruction=system_instruction,
        ):
            final_text += str(chunk or "")

    consume_task = asyncio.create_task(consume_stream())
    try:
        done, _ = await asyncio.wait(
            {consume_task, success_future},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=stream_timeout_sec,
        )

        if success_future in done:
            result = success_future.result()
            if not consume_task.done():
                consume_task.cancel()
                with suppress(asyncio.CancelledError):
                    await consume_task
            success_text = "记账成功"
            return TaskResult(
                task_id=task.task_id,
                worker_id=task.worker_id,
                ok=True,
                summary=success_text,
                payload={
                    "text": success_text,
                    "message": success_text,
                    "record_id": int(result.get("record_id") or 0),
                    "book_id": int(result.get("book_id") or 0),
                    "tool_called": called,
                },
            )

        if consume_task in done:
            try:
                await consume_task
            except Exception as exc:
                stream_error = str(exc or "").strip()
        else:
            stream_timed_out = True
            if not consume_task.done():
                consume_task.cancel()
                with suppress(asyncio.CancelledError):
                    await consume_task
    finally:
        if not consume_task.done():
            consume_task.cancel()
            with suppress(asyncio.CancelledError):
                await consume_task

    record_id, book_id = _extract_record_and_book_id(
        captured_data,
        fallback_book_id=metadata.get("accounting_book_id"),
    )

    if record_id > 0:
        success_text = "记账成功"
        return TaskResult(
            task_id=task.task_id,
            worker_id=task.worker_id,
            ok=True,
            summary=success_text,
            payload={
                "text": success_text,
                "message": success_text,
                "record_id": record_id,
                "book_id": book_id,
                "tool_called": called,
            },
        )

    detail = (
        last_error
        or stream_error
        or str(final_text or "").strip()
        or ("AI 识别超时，请稍后重试。" if stream_timed_out else "")
        or "AI 未能完成记账，请补充信息后重试。"
    )
    logger.warning(
        "web accounting auto-image failed task_id=%s detail=%s",
        task.task_id,
        detail[:200],
    )
    return TaskResult(
        task_id=task.task_id,
        worker_id=task.worker_id,
        ok=False,
        summary=detail[:200],
        error=detail[:200],
        payload={
            "text": detail[:500],
            "message": detail[:500],
            "record_id": 0,
            "book_id": book_id,
            "tool_called": called,
        },
    )
