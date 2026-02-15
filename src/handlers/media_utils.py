import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable

from core.platform.exceptions import (
    MediaDownloadUnavailableError,
    UnsupportedMediaOnPlatformError,
)
from core.platform.models import MessageType, UnifiedContext

logger = logging.getLogger(__name__)


@dataclass
class MediaInput:
    type: MessageType
    file_id: str
    mime_type: str
    caption: str = ""
    file_name: str | None = None
    file_size: int | None = None
    content: bytes | None = None
    meta: Dict[str, Any] = field(default_factory=dict)


def _default_mime(message_type: MessageType) -> str:
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
    if message_type == MessageType.STICKER:
        return "image/webp"
    if message_type == MessageType.ANIMATION:
        return "video/webm"
    return "application/octet-stream"


async def extract_media_input(
    ctx: UnifiedContext,
    expected_types: Iterable[MessageType] | None = None,
    auto_download: bool = False,
) -> MediaInput:
    message = ctx.message
    message_type = message.type
    expected_set = set(expected_types or [])

    if expected_set and message_type not in expected_set:
        raise UnsupportedMediaOnPlatformError(
            f"expected {sorted(item.value for item in expected_set)}, got {message_type.value}"
        )

    file_id = message.file_id
    mime_type = message.mime_type or _default_mime(message_type)
    file_name = message.file_name
    file_size = message.file_size
    caption = (message.caption or "").strip()

    meta: Dict[str, Any] = {
        "platform": message.platform,
        "width": message.width,
        "height": message.height,
        "duration": message.duration,
    }

    platform_event = ctx.platform_event

    # Telegram fallback: photo/sticker file_id is often only available on raw update payload.
    if message.platform == "telegram" and platform_event is not None:
        raw_message = getattr(platform_event, "message", None)
        if raw_message is not None:
            if message_type == MessageType.IMAGE and getattr(raw_message, "photo", None):
                photo = raw_message.photo[-1]
                file_id = file_id or getattr(photo, "file_id", None)
                file_size = file_size or getattr(photo, "file_size", None)
                mime_type = message.mime_type or "image/jpeg"
                meta["width"] = meta.get("width") or getattr(photo, "width", None)
                meta["height"] = meta.get("height") or getattr(photo, "height", None)
            elif message_type == MessageType.VIDEO and getattr(raw_message, "video", None):
                video = raw_message.video
                file_id = file_id or getattr(video, "file_id", None)
                file_size = file_size or getattr(video, "file_size", None)
                file_name = file_name or getattr(video, "file_name", None)
                mime_type = message.mime_type or getattr(video, "mime_type", None) or "video/mp4"
                meta["duration"] = meta.get("duration") or getattr(video, "duration", None)
            elif message_type == MessageType.VOICE and getattr(raw_message, "voice", None):
                voice = raw_message.voice
                file_id = file_id or getattr(voice, "file_id", None)
                file_size = file_size or getattr(voice, "file_size", None)
                mime_type = message.mime_type or getattr(voice, "mime_type", None) or "audio/ogg"
                meta["duration"] = meta.get("duration") or getattr(voice, "duration", None)
            elif message_type == MessageType.AUDIO and getattr(raw_message, "audio", None):
                audio = raw_message.audio
                file_id = file_id or getattr(audio, "file_id", None)
                file_size = file_size or getattr(audio, "file_size", None)
                file_name = file_name or getattr(audio, "file_name", None)
                mime_type = message.mime_type or getattr(audio, "mime_type", None) or "audio/mpeg"
                meta["duration"] = meta.get("duration") or getattr(audio, "duration", None)
            elif message_type == MessageType.DOCUMENT and getattr(raw_message, "document", None):
                document = raw_message.document
                file_id = file_id or getattr(document, "file_id", None)
                file_size = file_size or getattr(document, "file_size", None)
                file_name = file_name or getattr(document, "file_name", None)
                mime_type = (
                    message.mime_type
                    or getattr(document, "mime_type", None)
                    or "application/octet-stream"
                )
            elif message_type in (MessageType.STICKER, MessageType.ANIMATION) and getattr(
                raw_message, "sticker", None
            ):
                sticker = raw_message.sticker
                file_id = file_id or getattr(sticker, "file_id", None)
                file_size = file_size or getattr(sticker, "file_size", None)
                if getattr(sticker, "is_video", False):
                    mime_type = "video/webm"
                else:
                    mime_type = "image/webp"
                meta["is_animated"] = bool(getattr(sticker, "is_animated", False))
                meta["is_video"] = bool(getattr(sticker, "is_video", False))
                meta["width"] = meta.get("width") or getattr(sticker, "width", None)
                meta["height"] = meta.get("height") or getattr(sticker, "height", None)

    # Discord fallback: attachments might carry richer metadata.
    if message.platform == "discord" and platform_event is not None:
        attachments = getattr(platform_event, "attachments", None) or []
        if attachments:
            att = attachments[0]
            file_id = file_id or str(getattr(att, "id", "") or "")
            file_name = file_name or getattr(att, "filename", None)
            file_size = file_size or getattr(att, "size", None)
            mime_type = message.mime_type or getattr(att, "content_type", None) or mime_type
            duration = getattr(att, "duration_secs", None) or getattr(att, "duration", None)
            if duration is not None and not meta.get("duration"):
                meta["duration"] = duration

    if not file_id and message.file_url:
        file_id = message.file_url

    if not file_id:
        raise UnsupportedMediaOnPlatformError(
            f"no file reference available for {message.platform}:{message_type.value}"
        )

    media = MediaInput(
        type=message_type,
        file_id=str(file_id),
        mime_type=mime_type or _default_mime(message_type),
        caption=caption,
        file_name=file_name,
        file_size=file_size,
        meta=meta,
    )

    if auto_download:
        try:
            media.content = bytes(await ctx.download_file(media.file_id))
        except Exception as exc:
            logger.warning(
                "media download unavailable platform=%s type=%s file_id=%s err=%s",
                message.platform,
                message_type.value,
                media.file_id,
                exc,
            )
            raise MediaDownloadUnavailableError(
                f"download unavailable for {message.platform}:{message_type.value}"
            ) from exc

    return media
