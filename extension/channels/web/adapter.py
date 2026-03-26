from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from core.channel_runtime_store import channel_runtime_store
from core.platform.adapter import BotAdapter
from core.platform.exceptions import MessageSendError
from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from services.tts_service import synthesize_speech
from web_channel.store import (
    ack_inbound_event,
    append_outbound_event,
    claim_inbound_events,
    fail_inbound_event,
    get_file_record,
    get_session_projection,
    infer_message_type,
    load_file_bytes,
    register_artifact_file,
    upsert_session_message,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SEC = 1.0


class WebUnifiedContext(UnifiedContext):
    @property
    def callback_data(self) -> Optional[str]:
        raw = getattr(self, "_web_callback_data", "")
        return str(raw).strip() or None

    async def answer_callback(self, text: str = None, show_alert: bool = False):
        session_id = self._adapter._resolve_session_id(self)
        user_id = self.effective_user_id
        if not user_id or not session_id:
            return
        await append_outbound_event(
            owner_user_id=user_id,
            session_id=session_id,
            event_type="done",
            payload={
                "text": str(text or "").strip(),
                "show_alert": bool(show_alert),
            },
        )


class WebAdapter(BotAdapter):
    def __init__(self, *, poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC):
        super().__init__("web")
        self.poll_interval_sec = max(0.2, float(poll_interval_sec or DEFAULT_POLL_INTERVAL_SEC))
        self._poll_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._message_handler: Optional[Callable[[UnifiedContext], Any]] = None
        self._command_handlers: Dict[str, Callable[[UnifiedContext], Any]] = {}
        self._callback_handlers: List[Tuple[re.Pattern[str], Callable[[UnifiedContext], Any]]] = []
        self._user_data_store: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _safe_text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _is_auto_reply_payload(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return True
        if isinstance(value, dict):
            return "text" in value
        return False

    async def _auto_reply_if_needed(self, ctx: UnifiedContext, result: Any) -> None:
        if not self._is_auto_reply_payload(result):
            return
        await ctx.reply(result)

    def get_user_data(self, user_id: str) -> Dict[str, Any]:
        safe_user_id = self._safe_text(user_id)
        if safe_user_id not in self._user_data_store:
            self._user_data_store[safe_user_id] = {}
        return self._user_data_store[safe_user_id]

    def register_message_handler(self, handler: Callable[[UnifiedContext], Any]):
        self._message_handler = handler
        logger.info("Registered Web message handler")

    def on_message(self, filters_obj: Any, handler_func: Callable):
        _ = filters_obj
        self._message_handler = handler_func
        logger.info("Registered Web message handler")

    def on_command(self, command: str, handler: Callable[[UnifiedContext], Any], description: str = None, **kwargs):
        _ = description
        _ = kwargs
        safe_command = self._safe_text(command).lstrip("/")
        if safe_command:
            self._command_handlers[safe_command] = handler
            logger.info("Registered Web command: /%s", safe_command)

    def on_callback_query(self, pattern: str, handler: Callable[[UnifiedContext], Any]):
        self._callback_handlers.append((re.compile(pattern), handler))
        logger.info("Registered Web callback pattern: %s", pattern)

    async def start(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="web-channel-poll")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poll_task is None:
            return
        self._poll_task.cancel()
        try:
            await self._poll_task
        except asyncio.CancelledError:
            pass
        self._poll_task = None

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                claimed = await claim_inbound_events(limit=20)
                if not claimed:
                    await asyncio.sleep(self.poll_interval_sec)
                    continue
                for event in claimed:
                    await self._process_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Web adapter poll loop failed")
                await asyncio.sleep(self.poll_interval_sec)

    def _resolve_session_id(self, context: UnifiedContext) -> str:
        message = getattr(context, "message", None)
        user = getattr(message, "user", None)
        user_id = self._safe_text(getattr(user, "id", ""))
        raw_data = getattr(message, "raw_data", None)
        if isinstance(raw_data, dict):
            raw_session_id = self._safe_text(raw_data.get("session_id"))
            if raw_session_id:
                return raw_session_id
        session_id = ""
        if user_id:
            try:
                session_id = channel_runtime_store.get_session_id(
                    platform="web",
                    platform_user_id=user_id,
                )
            except Exception:
                session_id = ""
        if not session_id and isinstance(raw_data, dict):
            session_id = self._safe_text(raw_data.get("session_id"))
        if not session_id:
            session_id = self._safe_text(getattr(getattr(message, "chat", None), "id", ""))
        return session_id

    async def _process_event(self, event: dict[str, Any]) -> None:
        event_id = self._safe_text(event.get("id"))
        try:
            event_type = self._safe_text(event.get("type"))
            if event_type == "new_session":
                await ack_inbound_event(event_id, status="done")
                return
            ctx = await self._build_context(event)
            if event_type == "menu_action":
                handled = await self._dispatch_callback(ctx, self._safe_text((event.get("payload") or {}).get("callback_data")))
            elif event_type == "command":
                handled = await self._dispatch_command(ctx, self._safe_text((event.get("payload") or {}).get("text")))
            else:
                handled = await self._dispatch_command(ctx, ctx.message.content)
                if not handled and self._message_handler is not None:
                    result = await self._message_handler(ctx)
                    await self._auto_reply_if_needed(ctx, result)
                    handled = True
            if not handled and event_type == "menu_action":
                await append_outbound_event(
                    owner_user_id=ctx.effective_user_id,
                    session_id=self._resolve_session_id(ctx),
                    event_type="error",
                    payload={"message": "未找到可执行的菜单动作"},
                )
            await ack_inbound_event(event_id, status="done")
        except Exception as exc:
            logger.exception("Web adapter failed to process event %s", event_id)
            await fail_inbound_event(event_id, str(exc))
            payload = event.get("payload") or {}
            owner_user_id = self._safe_text(event.get("owner_user_id")) or self._safe_text(payload.get("user_id"))
            session_id = self._safe_text(event.get("session_id")) or self._safe_text(payload.get("session_id"))
            if owner_user_id and session_id:
                await append_outbound_event(
                    owner_user_id=owner_user_id,
                    session_id=session_id,
                    event_type="error",
                    payload={"message": str(exc)},
                )

    async def _build_context(self, event: dict[str, Any]) -> WebUnifiedContext:
        payload = dict(event.get("payload") or {})
        owner_user_id = self._safe_text(event.get("owner_user_id")) or self._safe_text(payload.get("user_id"))
        session_id = self._safe_text(event.get("session_id")) or self._safe_text(payload.get("session_id"))
        display_name = self._safe_text(payload.get("display_name")) or None
        username = self._safe_text(payload.get("username")) or None
        event_type = self._safe_text(event.get("type"))
        force_voice = event_type == "message_voice" or bool(payload.get("force_voice"))
        message_type = MessageType.TEXT
        file_id = self._safe_text(payload.get("file_id"))
        file_name = self._safe_text(payload.get("file_name")) or None
        mime_type = self._safe_text(payload.get("mime_type")) or None
        file_size = None
        try:
            file_size = int(payload.get("file_size")) if payload.get("file_size") is not None else None
        except Exception:
            file_size = None
        if event_type in {"message_file", "message_voice"} and file_id:
            if not mime_type or not file_name or file_size is None:
                file_record = await get_file_record(file_id)
                if isinstance(file_record, dict):
                    file_name = file_name or self._safe_text(file_record.get("name")) or None
                    mime_type = mime_type or self._safe_text(file_record.get("mime_type")) or None
                    try:
                        file_size = file_size or int(file_record.get("size") or 0)
                    except Exception:
                        pass
            message_type = infer_message_type(
                mime_type=mime_type,
                file_name=file_name,
                force_voice=force_voice,
            )
        text = self._safe_text(payload.get("text")) if event_type in {"message_text", "command"} else None
        caption = self._safe_text(payload.get("caption")) if event_type in {"message_file", "message_voice"} else None
        user = User(
            id=owner_user_id,
            username=username,
            first_name=display_name,
            raw_data={"session_id": session_id},
        )
        message = UnifiedMessage(
            id=self._safe_text(payload.get("message_id")) or event.get("id") or uuid.uuid4().hex,
            platform="web",
            user=user,
            chat=Chat(id=session_id or owner_user_id, type="private", title=display_name or "Web Chat"),
            date=datetime_now(),
            type=message_type,
            text=text,
            caption=caption,
            file_id=file_id or None,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            raw_data={
                **payload,
                "event_id": self._safe_text(event.get("id")),
                "session_id": session_id,
                "callback_data": self._safe_text(payload.get("callback_data")),
            },
        )
        platform_ctx = SimpleNamespace(user_data=self.get_user_data(owner_user_id))
        ctx = WebUnifiedContext(
            message=message,
            platform_ctx=platform_ctx,
            platform_event=payload,
            _adapter=self,
            user=user,
        )
        ctx._web_callback_data = self._safe_text(payload.get("callback_data"))
        return ctx

    async def _dispatch_command(self, ctx: UnifiedContext, text: str) -> bool:
        safe_text = self._safe_text(text)
        if not safe_text.startswith("/"):
            return False
        command = safe_text.split(" ", 1)[0][1:]
        handler = self._command_handlers.get(command)
        if handler is None:
            return False
        result = await handler(ctx)
        await self._auto_reply_if_needed(ctx, result)
        return True

    async def _dispatch_callback(self, ctx: UnifiedContext, callback_data: str) -> bool:
        safe_callback_data = self._safe_text(callback_data)
        if not safe_callback_data:
            return False
        ctx._web_callback_data = safe_callback_data
        for pattern, handler in self._callback_handlers:
            if not pattern.search(safe_callback_data):
                continue
            result = await handler(ctx)
            await self._auto_reply_if_needed(ctx, result)
            return True
        return False

    async def _emit_message_event(
        self,
        context: UnifiedContext,
        *,
        event_type: str,
        message_payload: dict[str, Any],
    ) -> SimpleNamespace:
        session_id = self._resolve_session_id(context)
        user_id = context.effective_user_id
        if not user_id or not session_id:
            raise MessageSendError("web reply target is missing user_id or session_id")
        stored = await upsert_session_message(
            user_id=user_id,
            session_id=session_id,
            message=message_payload,
        )
        await append_outbound_event(
            owner_user_id=user_id,
            session_id=session_id,
            event_type=event_type,
            payload={"message": stored},
        )
        return SimpleNamespace(id=stored["id"], message_id=stored["id"])

    async def _maybe_emit_tts(
        self,
        context: UnifiedContext,
        *,
        message_id: str,
        text: str,
    ) -> None:
        session_id = self._resolve_session_id(context)
        user_id = context.effective_user_id
        if not user_id or not session_id:
            return
        if not self._should_auto_tts(context, text=text):
            return
        audio_bytes = await synthesize_speech(text)
        if not audio_bytes:
            return
        artifact = await register_artifact_file(
            owner_user_id=user_id,
            session_id=session_id,
            source=audio_bytes,
            file_name=f"{message_id}.mp3",
            mime_type="audio/mpeg",
        )
        attachment = self._attachment_from_record(artifact, kind="audio")
        await upsert_session_message(
            user_id=user_id,
            session_id=session_id,
            message={
                "id": message_id,
                "role": "assistant",
                "attachments": [attachment],
                "message_type": "audio",
                "meta": {"tts_generated": True},
            },
        )
        await append_outbound_event(
            owner_user_id=user_id,
            session_id=session_id,
            event_type="audio_ready",
            payload={"message_id": message_id, "attachment": attachment},
        )

    def _should_auto_tts(self, context: UnifiedContext, *, text: str) -> bool:
        safe_text = self._safe_text(text)
        if len(safe_text) < 12:
            return False
        if safe_text.startswith(("🤔", "📄 正在", "🎤 正在", "⏳", "⚠️", "❌")):
            return False
        session_id = self._resolve_session_id(context)
        user_id = context.effective_user_id
        if not user_id or not session_id:
            return False
        projection = None
        try:
            projection = asyncio.get_running_loop()
        except Exception:
            projection = None
        _ = projection
        return False

    @staticmethod
    def _attachment_from_record(record: dict[str, Any], *, kind: str) -> dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "file_id": str(record.get("id") or ""),
            "kind": kind,
            "name": str(record.get("name") or ""),
            "mime_type": str(record.get("mime_type") or "application/octet-stream"),
            "size": int(record.get("size") or 0),
        }

    async def reply_text(self, context: UnifiedContext, text: str, **kwargs) -> Any:
        ui = kwargs.get("ui")
        message_id = self._safe_text(kwargs.get("message_id")) or uuid.uuid4().hex
        stored = await self._emit_message_event(
            context,
            event_type="message_created",
            message_payload={
                "id": message_id,
                "role": "assistant",
                "content": str(text or ""),
                "message_type": "text",
                "actions": list((ui or {}).get("actions") or []) if isinstance(ui, dict) else [],
                "meta": {"ui": ui} if ui else {},
            },
        )
        return stored

    async def edit_text(self, context: UnifiedContext, message_id: str, text: str, **kwargs) -> Any:
        ui = kwargs.get("ui")
        return await self._emit_message_event(
            context,
            event_type="message_updated",
            message_payload={
                "id": self._safe_text(message_id) or uuid.uuid4().hex,
                "role": "assistant",
                "content": str(text or ""),
                "message_type": "text",
                "actions": list((ui or {}).get("actions") or []) if isinstance(ui, dict) else [],
                "meta": {"ui": ui} if ui else {},
            },
        )

    async def reply_photo(
        self,
        context: UnifiedContext,
        photo: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        return await self._reply_attachment(
            context,
            content=photo,
            file_name=self._safe_text(kwargs.get("filename")) or "image.png",
            mime_type="image/png",
            caption=caption,
            kind="image",
        )

    async def reply_video(
        self,
        context: UnifiedContext,
        video: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        return await self._reply_attachment(
            context,
            content=video,
            file_name=self._safe_text(kwargs.get("filename")) or "video.mp4",
            mime_type="video/mp4",
            caption=caption,
            kind="video",
        )

    async def reply_document(
        self,
        context: UnifiedContext,
        document: Union[str, bytes],
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        inferred_name = self._safe_text(filename) or self._safe_text(kwargs.get("filename")) or "document.bin"
        guessed_mime = self._safe_text(mimetypes.guess_type(inferred_name)[0]) or "application/octet-stream"
        return await self._reply_attachment(
            context,
            content=document,
            file_name=inferred_name,
            mime_type=guessed_mime,
            caption=caption,
            kind="document",
        )

    async def reply_audio(
        self,
        context: UnifiedContext,
        audio: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        filename = self._safe_text(kwargs.get("filename")) or "audio.mp3"
        result = await self._reply_attachment(
            context,
            content=audio,
            file_name=filename,
            mime_type="audio/mpeg",
            caption=caption,
            kind="audio",
        )
        session_id = self._resolve_session_id(context)
        if context.effective_user_id and session_id:
            projection = await get_session_projection(context.effective_user_id, session_id)
            messages = list(projection.get("messages") or [])
            attachment = None
            for item in messages:
                if self._safe_text(item.get("id")) != self._safe_text(getattr(result, "id", "")):
                    continue
                attachments = item.get("attachments")
                if isinstance(attachments, list) and attachments:
                    attachment = attachments[0]
                break
            if attachment:
                await append_outbound_event(
                    owner_user_id=context.effective_user_id,
                    session_id=session_id,
                    event_type="audio_ready",
                    payload={
                        "message_id": getattr(result, "id", ""),
                        "attachment": attachment,
                    },
                )
        return result

    async def _reply_attachment(
        self,
        context: UnifiedContext,
        *,
        content: Union[str, bytes],
        file_name: str,
        mime_type: str,
        caption: Optional[str],
        kind: str,
    ) -> Any:
        session_id = self._resolve_session_id(context)
        user_id = context.effective_user_id
        if not user_id or not session_id:
            raise MessageSendError("web attachment target is missing user_id or session_id")
        artifact = await register_artifact_file(
            owner_user_id=user_id,
            session_id=session_id,
            source=content,
            file_name=file_name,
            mime_type=mime_type,
        )
        attachment = self._attachment_from_record(artifact, kind=kind)
        response = await self._emit_message_event(
            context,
            event_type="message_created",
            message_payload={
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": str(caption or ""),
                "message_type": kind,
                "attachments": [attachment],
            },
        )
        await append_outbound_event(
            owner_user_id=user_id,
            session_id=session_id,
            event_type="attachment_ready",
            payload={"message_id": getattr(response, "id", ""), "attachment": attachment},
        )
        return response

    async def delete_message(
        self,
        context: UnifiedContext,
        message_id: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        _ = chat_id
        _ = kwargs
        return await self._emit_message_event(
            context,
            event_type="message_updated",
            message_payload={
                "id": self._safe_text(message_id),
                "role": "assistant",
                "content": "",
                "status": "deleted",
                "message_type": "text",
            },
        )

    async def send_chat_action(
        self,
        context: UnifiedContext,
        action: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        _ = chat_id
        _ = kwargs
        session_id = self._resolve_session_id(context)
        user_id = context.effective_user_id
        if not user_id or not session_id:
            return None
        await append_outbound_event(
            owner_user_id=user_id,
            session_id=session_id,
            event_type="task_status",
            payload={"action": self._safe_text(action) or "typing"},
        )
        return None

    async def set_message_reaction(
        self,
        context: UnifiedContext,
        message_id: str,
        emoji: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        _ = chat_id
        _ = kwargs
        session_id = self._resolve_session_id(context)
        user_id = context.effective_user_id
        if not user_id or not session_id:
            return False
        await append_outbound_event(
            owner_user_id=user_id,
            session_id=session_id,
            event_type="reaction",
            payload={
                "message_id": self._safe_text(message_id),
                "emoji": self._safe_text(emoji),
            },
        )
        return True

    async def download_file(self, context: UnifiedContext, file_id: str, **kwargs) -> bytes:
        _ = context
        _ = kwargs
        return await load_file_bytes(file_id)


def datetime_now():
    from datetime import datetime

    return datetime.now().astimezone()
