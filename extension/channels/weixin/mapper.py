from __future__ import annotations

from datetime import datetime
from typing import Any

from core.platform.models import Chat, MessageType, UnifiedMessage, User


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int | None:
    try:
        rendered = int(value)
    except Exception:
        return None
    return rendered if rendered >= 0 else None


def _as_datetime(raw_ms: Any) -> datetime:
    try:
        millis = int(raw_ms)
        if millis > 0:
            return datetime.fromtimestamp(millis / 1000)
    except Exception:
        pass
    return datetime.now()


def _append_unique_text(parts: list[str], value: Any) -> None:
    text = _safe_text(value)
    if text and text not in parts:
        parts.append(text)


def _collect_textish_payload(payload: Any, *, depth: int = 1) -> list[str]:
    if not isinstance(payload, dict):
        return []

    parts: list[str] = []
    for key in (
        "text",
        "title",
        "desc",
        "description",
        "digest",
        "summary",
        "url",
        "link_url",
        "web_url",
        "jump_url",
        "jumpUrl",
        "link",
    ):
        _append_unique_text(parts, payload.get(key))

    if depth <= 0:
        return parts

    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        safe_key = _safe_text(key).lower()
        if safe_key.endswith("_item") or safe_key in {
            "article",
            "link",
            "page",
            "share",
            "source",
            "target",
            "webpage",
        }:
            for text in _collect_textish_payload(value, depth=depth - 1):
                _append_unique_text(parts, text)
    return parts


def _collect_text_parts(raw_message: dict[str, Any]) -> list[str]:
    items = raw_message.get("item_list") or []
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == 1:
            text_item = item.get("text_item") or {}
            _append_unique_text(parts, text_item.get("text"))
            continue
        if item_type in (2, 3, 4, 5):
            continue
        for key, value in item.items():
            if key == "type" or not isinstance(value, dict):
                continue
            for text in _collect_textish_payload(value):
                _append_unique_text(parts, text)
    return parts


def _default_mime(message_type: MessageType) -> str | None:
    if message_type == MessageType.IMAGE:
        return "image/jpeg"
    if message_type == MessageType.VIDEO:
        return "video/mp4"
    if message_type == MessageType.VOICE:
        return "audio/silk"
    if message_type == MessageType.DOCUMENT:
        return "application/octet-stream"
    return None


def _voice_mime_type(voice_item: dict[str, Any]) -> str:
    encode_type = _safe_int(voice_item.get("encode_type"))
    if encode_type == 7:
        return "audio/mpeg"
    if encode_type == 8:
        return "audio/ogg"
    if encode_type in {1, 2}:
        return "audio/wav"
    if encode_type == 5:
        return "audio/amr"
    return "audio/silk"


def _extract_media_ref(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    media = payload.get("media") or {}
    file_id = _safe_text(media.get("encrypt_query_param")) or None
    mime_type = _safe_text(payload.get("mime_type")) or None
    return file_id, mime_type


def _map_media_message(
    *,
    raw_message: dict[str, Any],
    item: dict[str, Any],
    user: User,
    chat: Chat,
    message_id: str,
    date: datetime,
    caption: str,
) -> UnifiedMessage:
    item_type = item.get("type")
    message_type = MessageType.TEXT
    file_id: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    duration: int | None = None

    if item_type == 2:
        message_type = MessageType.IMAGE
        image_item = item.get("image_item") or {}
        file_id, mime_type = _extract_media_ref(image_item)
        if not file_id:
            thumb_media = image_item.get("thumb_media") or {}
            file_id = _safe_text(thumb_media.get("encrypt_query_param")) or None
        file_size = (
            _safe_int(image_item.get("hd_size"))
            or _safe_int(image_item.get("mid_size"))
            or _safe_int(image_item.get("thumb_size"))
        )
    elif item_type == 3:
        message_type = MessageType.VOICE
        voice_item = item.get("voice_item") or {}
        file_id, mime_type = _extract_media_ref(voice_item)
        mime_type = mime_type or _voice_mime_type(voice_item)
        duration = _safe_int(voice_item.get("playtime"))
    elif item_type == 4:
        message_type = MessageType.DOCUMENT
        file_item = item.get("file_item") or {}
        file_id, mime_type = _extract_media_ref(file_item)
        file_name = _safe_text(file_item.get("file_name")) or None
        file_size = _safe_int(file_item.get("len"))
    elif item_type == 5:
        message_type = MessageType.VIDEO
        video_item = item.get("video_item") or {}
        file_id, mime_type = _extract_media_ref(video_item)
        if not file_id:
            thumb_media = video_item.get("thumb_media") or {}
            file_id = _safe_text(thumb_media.get("encrypt_query_param")) or None
        file_size = _safe_int(video_item.get("video_size"))
        duration = _safe_int(video_item.get("play_length"))

    return UnifiedMessage(
        id=message_id,
        platform="weixin",
        user=user,
        chat=chat,
        date=date,
        type=message_type,
        text=None,
        caption=caption or None,
        file_id=file_id,
        file_name=file_name,
        file_size=file_size,
        mime_type=mime_type or _default_mime(message_type),
        duration=duration,
        raw_data=dict(raw_message or {}),
    )


def map_weixin_message(raw_message: dict[str, Any]) -> UnifiedMessage:
    sender_id = _safe_text(raw_message.get("from_user_id")) or "unknown"
    sender_name = (
        _safe_text(raw_message.get("from_user_name"))
        or _safe_text(raw_message.get("from_user_nickname"))
        or _safe_text(raw_message.get("nickname"))
    )
    message_id = (
        _safe_text(raw_message.get("client_id"))
        or _safe_text(raw_message.get("msg_id"))
        or _safe_text(raw_message.get("message_id"))
        or str(int(datetime.now().timestamp() * 1000))
    )
    message_date = _as_datetime(raw_message.get("create_time_ms"))

    user = User(
        id=sender_id,
        username=sender_name or sender_id,
        first_name=sender_name or sender_id,
        is_bot=False,
        raw_data={"platform": "weixin"},
    )
    chat = Chat(
        id=sender_id,
        type="private",
        title=sender_name or None,
    )

    caption = "\n".join(_collect_text_parts(raw_message)).strip()
    items = raw_message.get("item_list") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") in (2, 3, 4, 5):
            return _map_media_message(
                raw_message=raw_message,
                item=item,
                user=user,
                chat=chat,
                message_id=message_id,
                date=message_date,
                caption=caption,
            )

    return UnifiedMessage(
        id=message_id,
        platform="weixin",
        user=user,
        chat=chat,
        date=message_date,
        type=MessageType.TEXT,
        text=caption or "(empty message)",
        raw_data=dict(raw_message or {}),
    )
