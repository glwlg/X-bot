from telegram import Update, Message as TGMessage
from telegram.ext import ContextTypes
from core.platform.models import UnifiedMessage, User, Chat, MessageType
import logging

logger = logging.getLogger(__name__)


def map_telegram_message(msg: TGMessage) -> UnifiedMessage:
    """Map a Telegram Message object to UnifiedMessage"""
    user = msg.from_user
    chat = msg.chat

    # Map User
    unified_user = User(
        id=str(user.id) if user else "unknown",
        username=user.username if user else None,
        first_name=user.first_name if user else None,
        last_name=user.last_name if user else None,
        language_code=user.language_code if user else None,
        is_bot=user.is_bot if user else False,
        raw_data=user.to_dict() if user else {},
    )

    # Map Chat
    unified_chat = Chat(
        id=str(chat.id) if chat else "unknown",
        type=chat.type if chat else "unknown",
        title=chat.title if chat else None,
        username=chat.username if chat else None,
    )

    # Determine Message Type and Content
    msg_type = MessageType.UNKNOWN
    text = msg.text
    caption = msg.caption
    file_id = None

    if msg.text:
        msg_type = MessageType.TEXT
    elif msg.photo:
        msg_type = MessageType.IMAGE
        file_id = msg.photo[-1].file_id  # Get largest photo
    elif msg.video:
        msg_type = MessageType.VIDEO
        file_id = msg.video.file_id
    elif msg.voice:
        msg_type = MessageType.VOICE
        file_id = msg.voice.file_id
    elif msg.audio:
        msg_type = MessageType.AUDIO
        file_id = msg.audio.file_id
    elif msg.document:
        msg_type = MessageType.DOCUMENT
        file_id = msg.document.file_id
    elif msg.sticker:
        msg_type = MessageType.STICKER
    elif msg.location:
        msg_type = MessageType.LOCATION
    elif msg.contact:
        msg_type = MessageType.CONTACT

    return UnifiedMessage(
        id=str(msg.message_id),
        platform="telegram",
        user=unified_user,
        chat=unified_chat,
        date=msg.date,
        type=msg_type,
        text=text,
        caption=caption,
        file_id=file_id,
        reply_to_message_id=str(msg.reply_to_message.message_id)
        if msg.reply_to_message
        else None,
        reply_to_message=map_telegram_message(msg.reply_to_message)
        if msg.reply_to_message
        else None,
        raw_data=msg.to_dict(),
    )


def map_update_to_message(update: Update) -> UnifiedMessage:
    """Maps a Telegram Update to a UnifiedMessage"""
    if not update.effective_message:
        raise ValueError("Update has no effective message")
    return map_telegram_message(update.effective_message)
