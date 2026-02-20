import logging
import asyncio
import io
import inspect
from typing import Any, Optional, Union, Callable, Dict
from telegram import Update, Bot
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from core.platform.adapter import BotAdapter
from core.platform.models import UnifiedContext
from core.platform.exceptions import MessageSendError, MessageEditNotSupported
from .mapper import map_update_to_message
from .formatter import markdown_to_telegram_html

logger = logging.getLogger(__name__)


class TelegramAdapter(BotAdapter):
    """Adapter for Telegram Bot API"""

    def __init__(self, application: Application):
        super().__init__("telegram")
        self.application = application
        self.bot: Bot = application.bot
        self._registered_commands = []

    def _render_ui(self, ui: Optional[Dict[str, Any]]) -> Optional[Any]:
        """Convert standard UI dict to Telegram ReplyMarkup"""
        if not ui or not isinstance(ui, dict):
            return None

        actions = ui.get("actions", [])
        if not actions:
            return None

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = []
        for row in actions:
            k_row = []
            for btn in row:
                # Support both dict and object (legacy)
                if isinstance(btn, dict):
                    text = btn.get("text", "Button")
                    callback_data = btn.get("callback_data")
                    url = btn.get("url")
                    k_row.append(
                        InlineKeyboardButton(
                            text=text, callback_data=callback_data, url=url
                        )
                    )
                else:
                    # Assume it's already an InlineKeyboardButton or similar
                    k_row.append(btn)
            keyboard.append(k_row)

        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        if "timed out" in text or "timeout" in text:
            return True
        name = exc.__class__.__name__.lower()
        return "timeout" in name

    async def _send_with_retry(
        self,
        sender: Callable[[], Any],
        *,
        max_attempts: int = 3,
        label: str = "send_message",
    ) -> Any:
        attempt = 0
        while True:
            attempt += 1
            try:
                result = sender()
                if inspect.isawaitable(result):
                    return await result
                return result
            except Exception as e:
                if attempt >= max_attempts or not self._is_timeout_error(e):
                    raise
                delay = 0.5 * attempt
                logger.warning(
                    "Telegram %s timeout, retrying (%s/%s) in %.1fs",
                    label,
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

    @staticmethod
    def _is_auto_reply_payload(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return True
        if isinstance(value, dict):
            # Structured unified reply payload: {"text": "...", "ui": {...}}
            return "text" in value
        return False

    async def _auto_reply_if_needed(
        self, unified_ctx: UnifiedContext, result: Any
    ) -> None:
        if not self._is_auto_reply_payload(result):
            return
        await unified_ctx.reply(result)

    async def start(self) -> None:
        """
        Start the bot using the configured Application.
        Note: logic often handled by run_polling in main, but we can wrap it.
        """
        await self.application.initialize()

        # Sync commands to Telegram UI
        if self._registered_commands:
            try:
                from telegram import BotCommand

                commands = []
                for cmd, desc in self._registered_commands:
                    # Description must be 3-256 chars
                    safe_desc = desc if desc else "Execute command"
                    if len(safe_desc) < 3:
                        safe_desc = "Execute command"
                    commands.append(BotCommand(cmd, safe_desc[:256]))

                await self.bot.set_my_commands(commands)
                logger.info(f"✅ Set {len(commands)} Telegram commands in menu")
            except Exception as e:
                logger.error(f"Failed to set Telegram commands: {e}")

        await self.application.start()
        await self.application.updater.start_polling()

    async def stop(self) -> None:
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

    async def reply_text(
        self, context: UnifiedContext, text: str, ui: Optional[Dict] = None, **kwargs
    ) -> Any:
        try:
            html_text = markdown_to_telegram_html(text)
            chat_id = context.message.chat.id

            reply_markup = kwargs.pop("reply_markup", None)
            if not reply_markup and ui:
                reply_markup = self._render_ui(ui)

            return await self._send_with_retry(
                lambda: self.bot.send_message(
                    chat_id=chat_id,
                    text=html_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                    **kwargs,
                ),
                label="reply_text",
            )
        except Exception as e:
            logger.error(f"Telegram reply_text failed: {e}")
            raise MessageSendError(str(e))

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        ui: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """Out-of-context message push helper used by scheduler/heartbeat."""
        try:
            html_text = markdown_to_telegram_html(text)
            reply_markup = kwargs.pop("reply_markup", None)
            if not reply_markup and ui:
                reply_markup = self._render_ui(ui)
            return await self._send_with_retry(
                lambda: self.bot.send_message(
                    chat_id=chat_id,
                    text=html_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                    **kwargs,
                ),
                label="send_message",
            )
        except Exception as e:
            logger.error(f"Telegram send_message failed: {e}")
            raise MessageSendError(str(e))

    async def send_document(
        self,
        chat_id: int | str,
        document: Union[str, bytes],
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            resolved_filename = str(filename or "heartbeat.md")
            formatted_caption = markdown_to_telegram_html(caption) if caption else None
            outgoing_doc: Union[str, bytes, io.BytesIO] = document
            if isinstance(document, bytes):
                file_obj = io.BytesIO(document)
                file_obj.name = resolved_filename
                outgoing_doc = file_obj
            return await self._send_with_retry(
                lambda: self.bot.send_document(
                    chat_id=chat_id,
                    document=outgoing_doc,
                    filename=resolved_filename,
                    caption=formatted_caption,
                    parse_mode="HTML",
                    **kwargs,
                ),
                label="send_document",
            )
        except Exception as e:
            logger.error(f"Telegram send_document failed: {e}")
            raise MessageSendError(str(e))

    async def edit_text(
        self,
        context: UnifiedContext,
        message_id: str,
        text: str,
        ui: Optional[Dict] = None,
        **kwargs,
    ) -> Any:
        try:
            html_text = markdown_to_telegram_html(text)
            chat_id = context.message.chat.id

            reply_markup = kwargs.pop("reply_markup", None)
            if not reply_markup and ui:
                reply_markup = self._render_ui(ui)

            return await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=html_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=reply_markup,
                **kwargs,
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                return None
            logger.error(f"Telegram edit_text failed: {e}")
            raise MessageSendError(str(e))

    async def reply_photo(
        self,
        context: UnifiedContext,
        photo: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            chat_id = context.message.chat.id
            formatted_caption = markdown_to_telegram_html(caption) if caption else None

            return await self.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=formatted_caption,
                parse_mode="HTML",
                **kwargs,
            )
        except Exception as e:
            logger.error(f"Telegram reply_photo failed: {e}")
            raise MessageSendError(str(e))

    async def reply_video(
        self,
        context: UnifiedContext,
        video: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            chat_id = context.message.chat.id
            formatted_caption = markdown_to_telegram_html(caption) if caption else None

            return await self.bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=formatted_caption,
                parse_mode="HTML",
                supports_streaming=kwargs.pop("supports_streaming", True),
                **kwargs,
            )
        except Exception as e:
            logger.error(f"Telegram reply_video failed: {e}")
            raise MessageSendError(str(e))

    async def reply_document(
        self,
        context: UnifiedContext,
        document: Union[str, bytes],
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            chat_id = context.message.chat.id
            formatted_caption = markdown_to_telegram_html(caption) if caption else None

            return await self.bot.send_document(
                chat_id=chat_id,
                document=document,
                filename=filename,
                caption=formatted_caption,
                parse_mode="HTML",
                **kwargs,
            )
        except Exception as e:
            logger.error(f"Telegram reply_document failed: {e}")
            raise MessageSendError(str(e))

    async def reply_audio(
        self,
        context: UnifiedContext,
        audio: Union[str, bytes],
        caption: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            chat_id = context.message.chat.id
            formatted_caption = markdown_to_telegram_html(caption) if caption else None

            return await self.bot.send_audio(
                chat_id=chat_id,
                audio=audio,
                caption=formatted_caption,
                parse_mode="HTML",
                **kwargs,
            )
        except Exception as e:
            logger.error(f"Telegram reply_audio failed: {e}")
            raise MessageSendError(str(e))

    async def delete_message(
        self,
        context: UnifiedContext,
        message_id: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            chat_id = chat_id or context.message.chat.id
            return await self.bot.delete_message(
                chat_id=chat_id, message_id=int(message_id), **kwargs
            )
        except Exception as e:
            logger.error(f"Telegram delete_message failed: {e}")
            # Don't raise error for delete failures, just log
            return False

    async def send_chat_action(
        self,
        context: UnifiedContext,
        action: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            chat_id = chat_id or context.message.chat.id
            return await self.bot.send_chat_action(
                chat_id=chat_id, action=action, **kwargs
            )
        except Exception as e:
            logger.error(f"Telegram send_chat_action failed: {e}")
            return False

    async def download_file(
        self, context: UnifiedContext, file_id: str, **kwargs
    ) -> bytes:
        try:
            file = await self.bot.get_file(file_id)
            return await file.download_as_bytearray()
        except Exception as e:
            logger.error(f"Telegram download_file failed: {e}")
            raise MessageSendError(f"Failed to download file: {e}")

    def on_command(self, command: str, handler_func: Callable, description: str = None):
        """Register a command handler safely wrapping it"""

        # Store for menu sync
        if not description:
            # Try to get docstring
            description = handler_func.__doc__

        self._registered_commands.append((command, description))

        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                unified_msg = map_update_to_message(update)
                unified_ctx = UnifiedContext(
                    message=unified_msg,
                    platform_ctx=context,
                    platform_event=update,
                    _adapter=self,
                )
                res = await handler_func(unified_ctx)
                await self._auto_reply_if_needed(unified_ctx, res)
                return res
            except Exception as e:
                logger.error(f"Error in unified handler wrapper: {e}", exc_info=True)

        self.application.add_handler(CommandHandler(command, wrapper))

    def on_message(self, filters_obj: Any, handler_func: Callable):
        """Register a message handler safely wrapping it"""

        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                unified_msg = map_update_to_message(update)
                unified_ctx = UnifiedContext(
                    message=unified_msg,
                    platform_ctx=context,
                    platform_event=update,
                    _adapter=self,
                )
                res = await handler_func(unified_ctx)
                await self._auto_reply_if_needed(unified_ctx, res)
                return res
            except Exception as e:
                logger.error(
                    f"Error in unified message handler wrapper: {e}", exc_info=True
                )

        self.application.add_handler(MessageHandler(filters_obj, wrapper))

    def on_callback_query(self, pattern: str, handler_func: Callable):
        """Register a callback query handler safely wrapping it"""

        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                # CallbackQuery updates might need a different mapping or just use the message inside
                # For now, map_update_to_message handles effective_message
                unified_msg = map_update_to_message(update)
                unified_ctx = UnifiedContext(
                    message=unified_msg,
                    platform_ctx=context,
                    platform_event=update,
                    _adapter=self,
                )
                res = await handler_func(unified_ctx)
                await self._auto_reply_if_needed(unified_ctx, res)
                return res
            except Exception as e:
                logger.error(
                    f"Error in unified callback handler wrapper: {e}", exc_info=True
                )

        self.application.add_handler(CallbackQueryHandler(wrapper, pattern=pattern))

    def create_callback_handler(
        self, pattern: str, handler_func: Callable
    ) -> CallbackQueryHandler:
        """Create a unified callback handler (for use in ConversationHandler)"""

        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                unified_msg = map_update_to_message(update)
                unified_ctx = UnifiedContext(
                    message=unified_msg,
                    platform_ctx=context,
                    platform_event=update,
                    _adapter=self,
                )
                res = await handler_func(unified_ctx)
                await self._auto_reply_if_needed(unified_ctx, res)
                return res
            except Exception as e:
                logger.error(f"Error in unified callback wrapper: {e}", exc_info=True)

        return CallbackQueryHandler(wrapper, pattern=pattern)

    def create_command_handler(
        self, command: str, handler_func: Callable
    ) -> CommandHandler:
        """Create a unified command handler"""

        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                unified_msg = map_update_to_message(update)
                unified_ctx = UnifiedContext(
                    message=unified_msg,
                    platform_ctx=context,
                    platform_event=update,
                    _adapter=self,
                )
                res = await handler_func(unified_ctx)
                await self._auto_reply_if_needed(unified_ctx, res)
                return res
            except Exception as e:
                logger.error(f"Error in unified command wrapper: {e}", exc_info=True)

        return CommandHandler(command, wrapper)

    def create_message_handler(
        self, filters_obj: Any, handler_func: Callable
    ) -> MessageHandler:
        """Create a unified message handler"""

        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                unified_msg = map_update_to_message(update)
                unified_ctx = UnifiedContext(
                    message=unified_msg,
                    platform_ctx=context,
                    platform_event=update,
                    _adapter=self,
                )
                res = await handler_func(unified_ctx)
                await self._auto_reply_if_needed(unified_ctx, res)
                return res
            except Exception as e:
                logger.error(f"Error in unified message wrapper: {e}", exc_info=True)

        return MessageHandler(filters_obj, wrapper)
