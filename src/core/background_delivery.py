from __future__ import annotations

import contextlib
import inspect
import io
import logging
import os
from datetime import datetime
from typing import Any

from core.heartbeat_store import heartbeat_store
from core.platform.registry import adapter_manager
from core.state_store import get_latest_session_id, get_session_entries, save_message
from services.md_converter import adapt_md_file_for_platform

logger = logging.getLogger(__name__)


def _normalize_history_user_id(value: Any) -> str:
    user_id = str(value or "").strip()
    if user_id in {"", "0", "system"}:
        return ""
    return user_id


async def _resolve_history_session_id(
    *,
    user_id: str,
    preferred_session_id: str = "",
) -> str:
    session_id = str(preferred_session_id or "").strip()
    if session_id:
        return session_id

    try:
        target = await heartbeat_store.get_delivery_target(user_id)
        session_id = str(target.get("session_id") or "").strip()
    except Exception:
        session_id = ""
    if session_id:
        return session_id

    try:
        return await get_latest_session_id(user_id)
    except Exception:
        logger.debug("Failed to resolve latest session for user=%s", user_id, exc_info=True)
        return ""


async def _record_background_history(
    *,
    user_id: str,
    text: str,
    preferred_session_id: str = "",
) -> None:
    safe_user_id = _normalize_history_user_id(user_id)
    payload = str(text or "").strip()
    if not safe_user_id or not payload:
        return

    session_id = await _resolve_history_session_id(
        user_id=safe_user_id,
        preferred_session_id=preferred_session_id,
    )
    if not session_id:
        return

    try:
        rows = await get_session_entries(safe_user_id, session_id)
        if rows:
            last = rows[-1]
            if (
                str(last.get("role") or "").strip().lower() == "model"
                and str(last.get("content") or "").strip() == payload
            ):
                return
        await save_message(safe_user_id, "model", payload, session_id)
    except Exception:
        logger.debug(
            "Failed to record background history user=%s session=%s",
            safe_user_id,
            session_id,
            exc_info=True,
        )


def split_background_chunks(text: str, *, limit: int = 3500) -> list[str]:
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


async def _send_document(
    *,
    adapter: Any,
    platform: str,
    chat_id: str,
    text: str,
    filename_prefix: str,
    caption: str,
) -> bool:
    payload = str(text or "").strip()
    if not payload:
        return True

    filename = f"{filename_prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    document_bytes = payload.encode("utf-8")
    with contextlib.suppress(Exception):
        document_bytes, filename = adapt_md_file_for_platform(
            file_bytes=document_bytes,
            filename=filename,
            platform=platform,
        )

    try:
        send_document = getattr(adapter, "send_document", None)
        if callable(send_document):
            result = send_document(
                chat_id=chat_id,
                document=document_bytes,
                filename=filename,
                caption=caption,
            )
            if inspect.isawaitable(result):
                await result
            return True

        bot = getattr(adapter, "bot", None)
        if platform == "telegram" and bot is not None:
            file_obj = io.BytesIO(document_bytes)
            file_obj.name = filename
            result = bot.send_document(
                chat_id=chat_id,
                document=file_obj,
                caption=caption,
            )
            if inspect.isawaitable(result):
                await result
            return True
    except Exception:
        return False
    return False


async def _send_text_chunk(
    *,
    adapter: Any,
    platform: str,
    chat_id: str,
    text: str,
    disable_web_page_preview: bool = True,
) -> bool:
    try:
        send_message = getattr(adapter, "send_message", None)
        if callable(send_message):
            result = send_message(
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=disable_web_page_preview,
            )
            if inspect.isawaitable(result):
                await result
            return True

        bot = getattr(adapter, "bot", None)
        if platform == "telegram" and bot is not None:
            html_payload = text
            with contextlib.suppress(Exception):
                from platforms.telegram.formatter import markdown_to_telegram_html

                html_payload = markdown_to_telegram_html(text)
            result = bot.send_message(
                chat_id=chat_id,
                text=html_payload,
                parse_mode="HTML",
                disable_web_page_preview=disable_web_page_preview,
            )
            if inspect.isawaitable(result):
                await result
            return True
    except Exception:
        return False
    return False


async def push_background_text(
    *,
    platform: str,
    chat_id: str,
    text: str,
    adapter: Any | None = None,
    filename_prefix: str = "background",
    file_caption: str = "📝 内容较长，完整结果见附件。",
    file_enabled: bool | None = None,
    file_threshold: int | None = None,
    max_text_chunks: int | None = None,
    disable_web_page_preview: bool = True,
    record_history: bool = False,
    history_user_id: str | int = "",
    history_session_id: str = "",
) -> bool:
    safe_platform = str(platform or "").strip().lower()
    safe_chat_id = str(chat_id or "").strip()
    payload = str(text or "").strip()
    if not safe_platform or not safe_chat_id:
        return False
    if not payload:
        return True

    if adapter is None:
        try:
            adapter = adapter_manager.get_adapter(safe_platform)
        except Exception:
            return False

    if file_enabled is None:
        file_enabled = os.getenv("BACKGROUND_PUSH_FILE_ENABLED", "true").lower() == "true"
    if file_threshold is None:
        try:
            file_threshold = int(os.getenv("BACKGROUND_PUSH_FILE_THRESHOLD", "12000"))
        except Exception:
            file_threshold = 12000
    if max_text_chunks is None:
        try:
            max_text_chunks = int(os.getenv("BACKGROUND_PUSH_MAX_TEXT_CHUNKS", "3"))
        except Exception:
            max_text_chunks = 3

    chunks = split_background_chunks(payload)
    if not chunks:
        return True

    safe_file_threshold = max(1, int(file_threshold or 12000))

    if (
        bool(file_enabled)
        and (
            len(chunks) > max(1, int(max_text_chunks or 1))
            or len(payload) > safe_file_threshold
        )
        and await _send_document(
            adapter=adapter,
            platform=safe_platform,
            chat_id=safe_chat_id,
            text=payload,
            filename_prefix=filename_prefix,
            caption=file_caption,
        )
    ):
        if record_history:
            await _record_background_history(
                user_id=str(history_user_id or ""),
                text=payload,
                preferred_session_id=history_session_id,
            )
        return True

    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        body = chunk
        if total > 1:
            body = f"[{idx}/{total}]\n{chunk}"
        sent = await _send_text_chunk(
            adapter=adapter,
            platform=safe_platform,
            chat_id=safe_chat_id,
            text=body,
            disable_web_page_preview=disable_web_page_preview,
        )
        if not sent:
            return False
    if record_history:
        await _record_background_history(
            user_id=str(history_user_id or ""),
            text=payload,
            preferred_session_id=history_session_id,
        )
    return True
