"""
DingTalk Message Mapper

将钉钉消息对象转换为 UnifiedMessage。
"""

from datetime import datetime
from typing import Any, Dict

from core.platform.models import Chat, MessageType, UnifiedMessage, User


def _to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            result = value.to_dict()
            if isinstance(result, dict):
                return result
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_") and not callable(item)
        }
    return {}


def _extract_first(mapping: Dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _normalize_msg_type(msg_type_str: str) -> MessageType:
    lowered = (msg_type_str or "text").lower().strip()
    if lowered in {"text", "richtext"}:
        return MessageType.TEXT
    if lowered in {"picture", "image", "photo"}:
        return MessageType.IMAGE
    if lowered == "video":
        return MessageType.VIDEO
    if lowered in {"audio", "voice"}:
        return MessageType.AUDIO if lowered == "audio" else MessageType.VOICE
    if lowered in {"file", "document"}:
        return MessageType.DOCUMENT
    return MessageType.UNKNOWN


def _extract_text(msg_data: Dict[str, Any], msg_type_str: str) -> str:
    lowered = (msg_type_str or "").lower()
    if lowered == "richtext":
        rich_text = _to_dict(msg_data.get("content")).get("richText", [])
        if not isinstance(rich_text, list):
            return ""
        text_parts = [str(item.get("text", "")).strip() for item in rich_text if isinstance(item, dict)]
        return "\n".join([item for item in text_parts if item])

    text_content = _to_dict(msg_data.get("text")).get("content")
    if text_content:
        return str(text_content).strip()

    content_text = _to_dict(msg_data.get("content")).get("text")
    if content_text:
        return str(content_text).strip()
    return ""


def _extract_file_ref(msg_data: Dict[str, Any]) -> tuple[str | None, str | None]:
    content = _to_dict(msg_data.get("content"))
    candidates = [
        _extract_first(
            content,
            [
                "downloadCode",
                "download_code",
                "pictureDownloadCode",
                "picture_download_code",
                "fileId",
                "file_id",
                "mediaId",
                "media_id",
                "url",
                "downloadUrl",
                "download_url",
            ],
        ),
        _extract_first(
            msg_data,
            [
                "downloadCode",
                "download_code",
                "pictureDownloadCode",
                "picture_download_code",
                "fileId",
                "file_id",
                "mediaId",
                "media_id",
                "url",
                "downloadUrl",
                "download_url",
            ],
        ),
    ]

    file_ref = next((str(item) for item in candidates if item), None)
    if not file_ref:
        return None, None

    if file_ref.startswith("http://") or file_ref.startswith("https://"):
        return file_ref, file_ref
    return file_ref, None


def _default_mime(message_type: MessageType) -> str | None:
    if message_type == MessageType.IMAGE:
        return "image/jpeg"
    if message_type == MessageType.VIDEO:
        return "video/mp4"
    if message_type == MessageType.AUDIO:
        return "audio/mpeg"
    if message_type == MessageType.VOICE:
        return "audio/ogg"
    if message_type == MessageType.DOCUMENT:
        return "application/octet-stream"
    return None


def map_dingtalk_message(
    msg_data: dict,
    conversation_id: str,
    sender_id: str,
    sender_nick: str,
    conversation_type: str = "1",
) -> UnifiedMessage:
    """
    Map DingTalk callback payload to UnifiedMessage.
    """
    msg_type_str = str(msg_data.get("msgtype") or msg_data.get("msgType") or "text")
    msg_type = _normalize_msg_type(msg_type_str)
    text_content = _extract_text(msg_data, msg_type_str)
    file_id, file_url = _extract_file_ref(msg_data)

    content = _to_dict(msg_data.get("content"))
    mime_type = _extract_first(
        content,
        ["mimeType", "mime_type", "contentType", "content_type"],
    )
    file_name = _extract_first(content, ["fileName", "file_name", "name"])
    file_size = _extract_first(content, ["fileSize", "file_size", "size"])

    unified_user = User(
        id=str(sender_id),
        username=sender_nick,
        first_name=sender_nick,
        is_bot=False,
    )

    chat_type = "private" if str(conversation_type) == "1" else "group"
    unified_chat = Chat(
        id=str(conversation_id),
        type=chat_type,
        title=None,
    )

    message_id = msg_data.get("msgId") or msg_data.get("msg_id")
    if not message_id:
        message_id = str(int(datetime.now().timestamp() * 1000))

    return UnifiedMessage(
        id=str(message_id),
        platform="dingtalk",
        user=unified_user,
        chat=unified_chat,
        date=datetime.now(),
        type=msg_type,
        text=text_content,
        file_id=str(file_id) if file_id else None,
        file_url=str(file_url) if file_url else None,
        file_name=str(file_name) if file_name else None,
        file_size=int(file_size) if isinstance(file_size, int) else None,
        mime_type=str(mime_type) if mime_type else _default_mime(msg_type),
        raw_data=_to_dict(msg_data),
    )


def map_chatbot_message(incoming_message) -> UnifiedMessage:
    """
    Map dingtalk_stream.ChatbotMessage to UnifiedMessage.
    """
    sender_id = getattr(incoming_message, "sender_staff_id", None) or getattr(
        incoming_message, "sender_id", "unknown"
    )
    sender_nick = getattr(incoming_message, "sender_nick", "Unknown")
    conversation_id = getattr(incoming_message, "conversation_id", "unknown")
    conversation_type = getattr(incoming_message, "conversation_type", "1")
    msg_id = getattr(incoming_message, "msg_id", None) or getattr(
        incoming_message, "message_id", None
    )

    msg_type_str = str(
        getattr(incoming_message, "message_type", None)
        or getattr(incoming_message, "msgtype", None)
        or getattr(incoming_message, "msg_type", None)
        or "text"
    )
    msg_type = _normalize_msg_type(msg_type_str)

    text_obj = _to_dict(getattr(incoming_message, "text", None))
    content_obj = _to_dict(getattr(incoming_message, "content", None))
    raw_data = _to_dict(incoming_message)

    text_content = ""
    if msg_type == MessageType.TEXT:
        text_content = str(
            text_obj.get("content")
            or content_obj.get("content")
            or content_obj.get("text")
            or ""
        ).strip()

    ref_candidates = [
        _extract_first(
            content_obj,
            [
                "downloadCode",
                "download_code",
                "pictureDownloadCode",
                "picture_download_code",
                "fileId",
                "file_id",
                "mediaId",
                "media_id",
                "url",
                "downloadUrl",
                "download_url",
            ],
        ),
        _extract_first(
            raw_data,
            [
                "download_code",
                "downloadCode",
                "picture_download_code",
                "pictureDownloadCode",
                "file_id",
                "fileId",
                "media_id",
                "mediaId",
                "url",
                "download_url",
                "downloadUrl",
            ],
        ),
    ]
    file_ref = next((str(item) for item in ref_candidates if item), None)
    file_url = None
    if file_ref and (file_ref.startswith("http://") or file_ref.startswith("https://")):
        file_url = file_ref

    mime_type = _extract_first(
        content_obj,
        ["mimeType", "mime_type", "contentType", "content_type"],
    )
    file_name = _extract_first(content_obj, ["fileName", "file_name", "name"])
    file_size = _extract_first(content_obj, ["fileSize", "file_size", "size"])

    unified_user = User(
        id=str(sender_id),
        username=sender_nick,
        first_name=sender_nick,
        is_bot=False,
    )

    chat_type = "private" if str(conversation_type) == "1" else "group"
    unified_chat = Chat(
        id=str(conversation_id),
        type=chat_type,
    )

    message_id = msg_id or str(int(datetime.now().timestamp() * 1000))

    return UnifiedMessage(
        id=str(message_id),
        platform="dingtalk",
        user=unified_user,
        chat=unified_chat,
        date=datetime.now(),
        type=msg_type,
        text=text_content,
        file_id=file_ref,
        file_url=file_url,
        file_name=str(file_name) if file_name else None,
        file_size=int(file_size) if isinstance(file_size, int) else None,
        mime_type=str(mime_type) if mime_type else _default_mime(msg_type),
        raw_data=raw_data,
    )
