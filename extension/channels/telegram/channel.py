from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    PicklePersistence,
    TypeHandler,
    filters,
)

from core.config import LOG_LEVEL, TELEGRAM_BOT_TOKEN
from core.extension_base import ChannelExtension

from .adapter import TelegramAdapter
from ..common import COMMON_CALLBACK_PATTERN, button_callback
from handlers import handle_ai_chat, handle_ai_photo, handle_ai_video, handle_sticker_message
from handlers.document_handler import handle_document
from handlers.voice_handler import handle_voice_message

logger = logging.getLogger(__name__)


async def log_update(update: Update, context):
    _ = context
    if update.callback_query:
        logger.info(
            "👉 RECEIVED CALLBACK: %s from user %s",
            update.callback_query.data,
            update.effective_user.id,
        )
    elif update.message:
        logger.info(
            "📩 RECEIVED MESSAGE: %s from user %s",
            update.message.text,
            update.effective_user.id,
        )


class TelegramChannelExtension(ChannelExtension):
    name = "telegram_channel"
    platform_name = "telegram"
    priority = 10

    def enabled(self, runtime) -> bool:
        _ = runtime
        return bool(TELEGRAM_BOT_TOKEN)

    def register(self, runtime) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

        persistence = PicklePersistence(filepath="data/bot_persistence.pickle")
        tg_app = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .persistence(persistence)
            .concurrent_updates(True)
            .read_timeout(60)
            .write_timeout(120)
            .build()
        )
        tg_app.add_handler(TypeHandler(Update, log_update), group=-1)

        adapter = runtime.register_adapter(TelegramAdapter(tg_app))
        adapter.on_callback_query(COMMON_CALLBACK_PATTERN, button_callback)
        adapter.on_message(filters.PHOTO, handle_ai_photo)
        adapter.on_message(filters.VIDEO, handle_ai_video)
        adapter.on_message(filters.VOICE | filters.AUDIO, handle_voice_message)
        adapter.on_message(filters.Document.ALL, handle_document)
        adapter.on_message(filters.Sticker.ALL, handle_sticker_message)
        adapter.on_message(filters.TEXT & ~filters.COMMAND, handle_ai_chat)
