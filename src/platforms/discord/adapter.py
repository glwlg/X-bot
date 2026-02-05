import logging
import discord
from typing import Any, Optional, Callable, Dict, List, Tuple
import re
from telegram import (
    Update as LegacyUpdate,
)  # Keep for legacy compatibility if needed, but we try to avoid it
from telegram import InlineKeyboardMarkup
# logic: We map Discord interactions to UnifiedContext

from core.platform.adapter import BotAdapter
from core.platform.models import UnifiedContext, UnifiedMessage, User, Chat, MessageType
from core.platform.exceptions import MessageSendError

logger = logging.getLogger(__name__)


class InnerDiscordClient(discord.Client):
    def __init__(self, adapter, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        from discord import app_commands

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        logger.info("🪝 Discord Client Setup Hook triggered")
        try:
            await self.tree.sync()
            logger.info("✅ Synced Discord Application Commands (Global)")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}", exc_info=True)


class DiscordAdapter(BotAdapter):
    """
    Discord Platform Adapter using discord.py
    """

    def __init__(self, token: str):
        super().__init__("discord")
        self.token = token
        intents = discord.Intents.default()
        intents.message_content = True  # Required for reading message content

        # Use custom client
        self.client = InnerDiscordClient(self, intents=intents)
        # Expose tree for adapter methods
        self.tree = self.client.tree

        # Internal handlers registry
        self._message_handler: Optional[Callable[[UnifiedContext], Any]] = None
        self._command_handlers: Dict[str, Callable[[UnifiedContext], Any]] = {}
        self._callback_handlers: List[
            Tuple[re.Pattern, Callable[[UnifiedContext], Any]]
        ] = []

        # User Data Storage (In-Memory for now)
        self._user_data_store: Dict[str, Dict[str, Any]] = {}

        # Setup discord events
        self._setup_events()

    def _setup_events(self):
        @self.client.event
        async def on_ready():
            logger.info(
                f"Logged in as Discord Bot: {self.client.user} (ID: {self.client.user.id})"
            )
            # Sync removed from on_ready, moved to setup_hook

        @self.client.event
        async def on_message(message: discord.Message):
            # Ignore own messages
            if message.author == self.client.user:
                return

            # Map to UnifiedContext
            try:
                # 1. Map Message
                unified_msg = await self._map_message(message)

                # 2. Create Context
                context = UnifiedContext(
                    message=unified_msg,
                    platform_event=message,  # Store raw event
                    platform_ctx=self.client,  # Store client as context
                    _adapter=self,
                )

                # 3. Check for Commands (Legacy Text Fallback)
                if unified_msg.text and unified_msg.text.startswith("/"):
                    parts = unified_msg.text.split(" ", 1)
                    command = parts[0][1:]  # Remove slash
                    if command in self._command_handlers:
                        # Optional: If slash command exists, maybe ignore text command to avoid double trigger?
                        # But Slash Command event is distinct from Message event.
                        # Slash Command triggers 'on_interaction', Message triggers 'on_message'.
                        # So they won't conflict unless user types manually.
                        # We keep this for backward compatibility or direct text use.
                        await self._command_handlers[command](context)
                        return

                # 4. Default Message Handler
                if self._message_handler:
                    await self._message_handler(context)

            except Exception as e:
                logger.error(f"Error processing Discord message: {e}", exc_info=True)

        @self.client.event
        async def on_interaction(interaction: discord.Interaction):
            """Handle all interactions including button clicks"""
            # Only handle component interactions (buttons)
            if interaction.type != discord.InteractionType.component:
                return

            custom_id = interaction.data.get("custom_id") if interaction.data else None
            if not custom_id:
                return

            # Route to our callback handler
            await self._handle_button_interaction(interaction, custom_id)

    # Rest of the class...

    async def _map_message(self, message: discord.Message) -> UnifiedMessage:
        """Map Discord Message to UnifiedMessage"""
        # Determine message type and file info
        msg_type = MessageType.TEXT
        file_id = None
        file_url = None

        if message.attachments:
            att = message.attachments[0]
            file_id = str(att.id)
            file_url = att.url

            # Simple heuristic
            ct = att.content_type
            if ct:
                if ct.startswith("image"):
                    msg_type = MessageType.IMAGE
                elif ct.startswith("video"):
                    msg_type = MessageType.VIDEO
                elif ct.startswith("audio"):
                    msg_type = MessageType.AUDIO
                else:
                    msg_type = MessageType.DOCUMENT

        return UnifiedMessage(
            id=str(message.id),
            platform="discord",
            text=message.content,
            date=message.created_at,
            type=msg_type,
            file_id=file_id,
            file_url=file_url,
            chat=Chat(
                id=str(message.channel.id),
                type="group"
                if isinstance(
                    message.channel, (discord.GroupChannel, discord.TextChannel)
                )
                else "private",
                title=getattr(message.channel, "name", None),
            ),
            user=User(
                id=str(message.author.id),
                username=message.author.name,
                first_name=message.author.display_name,
                is_bot=message.author.bot,
            ),
            reply_to_message=await self._map_message(message.reference.resolved)
            if message.reference
            and isinstance(message.reference.resolved, discord.Message)
            else None,
        )

    def _telegram_markup_to_discord_view(
        self, reply_markup: Any
    ) -> Optional[discord.ui.View]:
        """Convert Telegram InlineKeyboardMarkup to Discord View"""
        if not reply_markup or not isinstance(reply_markup, InlineKeyboardMarkup):
            return None

        view = discord.ui.View(timeout=None)

        for row in reply_markup.inline_keyboard:
            for button in row:
                # Map Telegram button to Discord button
                url = button.url
                callback_data = button.callback_data
                label = button.text

                style = discord.ButtonStyle.secondary
                if url:
                    style = discord.ButtonStyle.link
                else:
                    style = discord.ButtonStyle.primary

                # Create Discord Button
                discord_btn = discord.ui.Button(
                    style=style,
                    label=label,
                    url=url,
                    custom_id=str(callback_data) if not url else None,
                )

                # Note: callbacks are handled via on_interaction event
                view.add_item(discord_btn)

        return view

    def _actions_to_discord_view(self, actions: list) -> Optional[discord.ui.View]:
        """
        Convert unified actions format to Discord View.
        actions format: [[{"text": "Label", "callback_data": "data"}, ...], ...]
        """
        if not actions:
            return None

        view = discord.ui.View(timeout=None)

        for row in actions:
            for button in row:
                label = button.get("text", "Button")
                callback_data = button.get("callback_data")
                url = button.get("url")

                style = discord.ButtonStyle.secondary
                if url:
                    style = discord.ButtonStyle.link
                else:
                    style = discord.ButtonStyle.primary

                # Create Discord Button
                discord_btn = discord.ui.Button(
                    style=style,
                    label=label,
                    url=url,
                    custom_id=str(callback_data) if not url else None,
                )

                # Note: callbacks are handled via on_interaction event
                view.add_item(discord_btn)

        return view

    async def _generic_button_callback(self, interaction: discord.Interaction):
        """Handle dynamic button clicks"""
        # Acknowledge to prevent failure state (some handlers might need to followup)
        # We defer properly if handler takes time.
        # But here we just route it.

        custom_id = interaction.data.get("custom_id")
        if not custom_id:
            return

        try:
            # Create a Context for this callback
            # We need to map the interaction message to UnifiedMessage
            if interaction.message:
                unified_msg = await self._map_message(interaction.message)
            else:
                # Ephemeral?
                unified_msg = UnifiedMessage(
                    id="interaction",
                    platform="discord",
                    type=MessageType.TEXT,
                    user=User(
                        id=str(interaction.user.id), username=interaction.user.name
                    ),
                    chat=Chat(
                        id=str(interaction.channel_id), type="private"
                    ),  # minimal
                    date=interaction.created_at,
                )

            # Create Unified Context
            # Explicitly map the interaction user as the effective user
            effective_user = User(
                id=str(interaction.user.id),
                username=interaction.user.name,
                first_name=interaction.user.display_name,
                is_bot=interaction.user.bot,
            )

            context = UnifiedContext(
                message=unified_msg,
                platform_event=interaction,  # Store raw interaction
                platform_ctx=self.client,
                _adapter=self,
                user=effective_user,
            )

            # Also inject 'callback_query' shim for legacy handlers that access ctx.platform_event.callback_query
            # This is a bit hacky but needed for seamless migration
            # Or we assume handlers use UnifiedContext abstraction... currently they DON'T all do.
            # They access update.callback_query.data

            # Let's see if we can shim it dynamically?
            # For now, let's rely on handlers accessing context attributes we might mock?
            # No, better: The handlers should be migrated to check `ctx.callback_data`?
            # For now, let's just dispatch logic.

            # Find matching handler
            matched = False
            for pattern, handler in self._callback_handlers:
                if pattern.match(custom_id):
                    # Hack: attach 'match' object?
                    # Or just call handler(context)

                    # Check if we need to defer?
                    if not interaction.response.is_done():
                        try:
                            await interaction.response.defer()
                        except (discord.NotFound, discord.HTTPException) as e:
                            logger.warning(f"Failed to defer callback {custom_id}: {e}")
                            return

                    await handler(context)
                    matched = True
                    break

            if not matched:
                logger.warning(f"No handler found for callback: {custom_id}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ Unknown action", ephemeral=True
                    )

        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Error: {e}", ephemeral=True
                )
            else:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    async def _handle_button_interaction(
        self, interaction: discord.Interaction, custom_id: str
    ):
        """Handle button interactions from on_interaction event"""
        try:
            # Create a Context for this callback
            if interaction.message:
                unified_msg = await self._map_message(interaction.message)
            else:
                unified_msg = UnifiedMessage(
                    id="interaction",
                    platform="discord",
                    type=MessageType.TEXT,
                    user=User(
                        id=str(interaction.user.id), username=interaction.user.name
                    ),
                    chat=Chat(id=str(interaction.channel_id), type="private"),
                    date=interaction.created_at,
                )

            effective_user = User(
                id=str(interaction.user.id),
                username=interaction.user.name,
                first_name=interaction.user.display_name,
                is_bot=interaction.user.bot,
            )

            context = UnifiedContext(
                message=unified_msg,
                platform_event=interaction,
                platform_ctx=self.client,
                _adapter=self,
                user=effective_user,
            )

            # Find matching handler
            matched = False
            for pattern, handler in self._callback_handlers:
                if pattern.match(custom_id):
                    if not interaction.response.is_done():
                        try:
                            await interaction.response.defer()
                        except (discord.NotFound, discord.HTTPException) as e:
                            logger.warning(
                                f"Failed to defer button interaction {custom_id}: {e}"
                            )
                            return

                    result = await handler(context)

                    # 处理 handler 返回值
                    if result:
                        text = ""
                        if isinstance(result, dict):
                            text = result.get("text", "")
                        elif isinstance(result, str):
                            text = result

                        if text:
                            from .formatter import markdown_to_discord_compat

                            text = markdown_to_discord_compat(text)
                            await interaction.followup.send(text, ephemeral=True)

                    matched = True
                    break

            if not matched:
                logger.warning(f"No handler found for callback: {custom_id}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ Unknown action", ephemeral=True
                    )

        except Exception as e:
            logger.error(f"Button interaction error: {e}", exc_info=True)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Error: {e}", ephemeral=True
                    )
                else:
                    await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            except Exception:
                pass

    async def start(self):
        """Start the bot polling/connection"""
        # discord.py client.start is an async blocking call, so we ideally run it in a task
        # But BotAdapter.start might be expected to be blocking or managed by runner.
        # usually: await client.start(token)
        import asyncio

        asyncio.create_task(self.client.start(self.token))

    async def stop(self):
        await self.client.close()

    async def send_message(self, chat_id: int | str, text: str, **kwargs) -> Any:
        """
        主动发送消息（用于调度器推送）。
        chat_id 可以是：
        - 用户 ID：会尝试创建 DM 通道并发送
        - 频道 ID：直接发送到该频道
        """
        from .formatter import markdown_to_discord_compat

        text = markdown_to_discord_compat(text)
        chat_id = int(chat_id)

        try:
            # 1. 尝试作为频道 ID 获取
            channel = self.client.get_channel(chat_id)
            if not channel:
                # Cache miss, 尝试从 API 获取
                try:
                    channel = await self.client.fetch_channel(chat_id)
                except discord.NotFound, discord.Forbidden:
                    pass

            if channel:
                return await channel.send(text)

            # 2. 如果不是频道，尝试作为用户 ID 获取并创建 DM
            user = self.client.get_user(chat_id)
            if not user:
                # 尝试 fetch（可能不在缓存中）
                try:
                    user = await self.client.fetch_user(chat_id)
                except discord.NotFound:
                    logger.error(f"Discord ID {chat_id} not found as Channel or User")
                    return None

            if user:
                dm_channel = await user.create_dm()
                return await dm_channel.send(text)

            logger.error(f"Could not resolve Discord chat_id: {chat_id}")
            return None

        except discord.Forbidden:
            logger.error(f"Discord: Cannot send message to {chat_id} (Forbidden)")
            return None
        except Exception as e:
            logger.error(f"Discord send_message error: {e}", exc_info=True)
            raise

    async def reply_text(self, context: UnifiedContext, text: str, **kwargs) -> Any:
        try:
            channel = context.platform_event.channel

            # Format Markdown for Discord
            from .formatter import markdown_to_discord_compat

            text = markdown_to_discord_compat(text)

            # Check for reply_markup
            view = self._telegram_markup_to_discord_view(kwargs.get("reply_markup"))

            # IMPORTANT: discord.py interaction methods fail if view is None (AttributeError: is_finished)
            # We must use MISSING to indicate "no view"
            view_arg = view if view is not None else discord.utils.MISSING

            # Check for Interaction first (Slash Commands)
            if isinstance(context.platform_event, discord.Interaction):
                interaction: discord.Interaction = context.platform_event
                # If not responded, use response.send_message
                if not interaction.response.is_done():
                    await interaction.response.send_message(text, view=view_arg)
                    return await interaction.original_response()  # Hack to return something waitable if needed, but simple return is ok
                else:
                    # Use followup
                    return await interaction.followup.send(text, view=view_arg)

            # Fallback to Channel send (Message event)
            return await channel.send(text, view=view_arg)
        except Exception as e:
            logger.error(f"Discord reply error: {e}")
            raise MessageSendError(str(e))

    async def edit_text(
        self, context: UnifiedContext, message_id: str, text: str, **kwargs
    ) -> Any:
        try:
            # We need the channel to fetch message, or if context.platform_event is the message we can edit it?
            # context.platform_event is the *incoming* message. To edit a bot message we need that message object.
            # Unlike TG, Discord needs channel + message_id to fetch partial message or use reference.

            # Since UnifiedContext doesn't store the "previous sent message object" generically,
            # we rely on message_id being passed. In Discord, we typically need to find it in channel.
            channel = context.platform_event.channel
            msg = await channel.fetch_message(int(message_id))
            # Check for reply_markup
            view = self._telegram_markup_to_discord_view(kwargs.get("reply_markup"))

            # Interaction specific edit?
            if isinstance(context.platform_event, discord.Interaction):
                # If we are editing the *original response* of the interaction
                interaction: discord.Interaction = context.platform_event
                # But message_id passed might be different.
                # If message_id matches interaction.original_response().id ? Hard to check.
                # Let's rely on fetch_message mostly, but if it is the interaction response we can use edit_original_response
                pass

            return await msg.edit(content=text, view=view)
        except Exception as e:
            logger.error(f"Discord edit error: {e}")
            raise MessageSendError(str(e))

    async def delete_message(
        self,
        context: UnifiedContext,
        message_id: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            channel = context.platform_event.channel
            msg = await channel.fetch_message(int(message_id))
            await msg.delete()
            return True
        except Exception as e:
            logger.error(f"Discord delete error: {e}")
            return False

    async def send_chat_action(
        self,
        context: UnifiedContext,
        action: str,
        chat_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            channel = context.platform_event.channel
            if action == "typing":
                await channel.typing()
        except Exception as e:
            logger.error(f"Discord chat action error: {e}")

    async def download_file(
        self, context: UnifiedContext, file_id: str, **kwargs
    ) -> bytes:
        # In Discord, file_id might probably be the Attachment URL or Proxy URL since we don't have persistent internal IDs like TG?
        # Or we map attachment ID to object.
        # For simplicity, let's assume mapping logic stores URL or we iterate attachments in the context message.

        # Strategy: Iterate attachments in current message (platform_event) and find matching ID.
        message = context.platform_event
        target_attachment = None

        if message.attachments:
            for att in message.attachments:
                if str(att.id) == str(file_id):
                    target_attachment = att
                    break

        if target_attachment:
            return await target_attachment.read()

        raise MessageSendError(f"File {file_id} not found in message attachments")

    async def reply_photo(
        self, context: UnifiedContext, photo: Any, caption: str = None, **kwargs
    ) -> Any:
        try:
            channel = context.platform_event.channel
            import io

            if isinstance(photo, bytes):
                photo = io.BytesIO(photo)

            file = discord.File(fp=photo, filename="image.jpg")
            return await channel.send(content=caption, file=file)
        except Exception as e:
            raise MessageSendError(f"Discord reply_photo error: {e}")

    async def reply_video(
        self, context: UnifiedContext, video: Any, caption: str = None, **kwargs
    ) -> Any:
        try:
            channel = context.platform_event.channel
            import io

            if isinstance(video, bytes):
                video = io.BytesIO(video)

            file = discord.File(fp=video, filename="video.mp4")
            return await channel.send(content=caption, file=file)
        except Exception as e:
            raise MessageSendError(f"Discord reply_video error: {e}")

    async def reply_audio(
        self, context: UnifiedContext, audio: Any, caption: str = None, **kwargs
    ) -> Any:
        try:
            channel = context.platform_event.channel
            import io

            if isinstance(audio, bytes):
                audio = io.BytesIO(audio)

            file = discord.File(fp=audio, filename="audio.mp3")
            return await channel.send(content=caption, file=file)
        except Exception as e:
            raise MessageSendError(f"Discord reply_audio error: {e}")

    async def reply_document(
        self,
        context: UnifiedContext,
        document: Any,
        filename: str = "document",
        caption: str = None,
        **kwargs,
    ) -> Any:
        try:
            channel = context.platform_event.channel
            import io

            if isinstance(document, bytes):
                document = io.BytesIO(document)

            file = discord.File(fp=document, filename=filename)
            return await channel.send(content=caption, file=file)
        except Exception as e:
            raise MessageSendError(f"Discord reply_document error: {e}")

    async def _handle_slash_command(
        self,
        interaction: discord.Interaction,
        command: str,
        params: Optional[str],
        handler: Callable,
    ):
        """Generic wrapper for slash commands"""
        try:
            # Defer immediately to prevent "Unknown interaction" (timeout > 3s)
            # This gives us 15 minutes to respond via followup.
            try:
                await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException) as e:
                logger.warning(
                    f"Slash command interaction expired/invalid ({command}): {e}"
                )
                return

            # Reconstruct text: "/command params"
            text_content = f"/{command}"
            if params:
                text_content += f" {params}"

            # Map Interaction to Message (Fake Message)
            # User
            effective_user = User(
                id=str(interaction.user.id),
                username=interaction.user.name,
                first_name=interaction.user.display_name,
                is_bot=interaction.user.bot,
            )

            # Chat
            chat_type = "private"
            if interaction.channel:
                if isinstance(
                    interaction.channel, (discord.GroupChannel, discord.TextChannel)
                ):
                    chat_type = "group"

            unified_msg = UnifiedMessage(
                id=str(interaction.id),  # Interaction ID acts as Message ID roughly
                platform="discord",
                text=text_content,
                date=interaction.created_at,
                type=MessageType.TEXT,
                user=effective_user,
                chat=Chat(
                    id=str(interaction.channel_id)
                    if interaction.channel_id
                    else "unknown",
                    type=chat_type,
                    title=getattr(interaction.channel, "name", None),
                ),
            )

            context = UnifiedContext(
                message=unified_msg,
                platform_event=interaction,  # IMPORTANT: Pass interaction as event
                platform_ctx=self.client,
                _adapter=self,
                user=effective_user,
            )

            result = await handler(context)

            # 处理 handler 返回值 - 如果有返回则发送响应
            if result:
                text = ""
                view = discord.utils.MISSING

                if isinstance(result, dict):
                    text = result.get("text", "")
                    ui = result.get("ui", {})
                    if ui and ui.get("actions"):
                        # 转换 actions 为 Discord View
                        view = self._actions_to_discord_view(ui["actions"])
                elif isinstance(result, str):
                    text = result

                if text:
                    from .formatter import markdown_to_discord_compat

                    text = markdown_to_discord_compat(text)
                    await interaction.followup.send(text, view=view)

        except Exception as e:
            logger.error(f"Slash command error: {e}", exc_info=True)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Error: {e}", ephemeral=True
                    )
                else:
                    await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            except Exception:
                pass  # 避免错误处理时再次出错

    def on_command(
        self,
        command: str,
        handler: Callable[[UnifiedContext], Any],
        description: str = None,
        **kwargs,
    ):
        self._command_handlers[command] = handler

        # Register as Slash Command
        from discord import app_commands

        # We need a description. Default generic.
        if not description:
            # Try to get docstring
            description = handler.__doc__ or "Execute command"

        # Ensure description is not empty and fits limits (100 chars for Discord)
        if len(description) > 100:
            description = description[:97] + "..."
        if not description:
            description = "Execute command"

        # We Define the callback dynamically
        # Note: 'args' is the optional parameter string
        async def slash_callback(
            interaction: discord.Interaction, args: Optional[str] = None
        ):
            await self._handle_slash_command(interaction, command, args, handler)

        # Set metadata
        slash_callback.__name__ = command

        # Create Command
        # Note: We must bind it to the tree
        discord_cmd = app_commands.Command(
            name=command, description=description, callback=slash_callback
        )

        self.tree.add_command(discord_cmd)

        logger.info(
            f"Registered Discord command (Slash+Text): /{command} ({description})"
        )

    # Alias for backward compatibility if needed
    register_command = on_command

    def register_message_handler(self, handler: Callable[[UnifiedContext], Any]):
        self._message_handler = handler
        logger.info("Registered Discord message handler")

    def get_user_data(self, user_id: str) -> Dict[str, Any]:
        """Retrieve or create user_data dict for a user"""
        if user_id not in self._user_data_store:
            self._user_data_store[user_id] = {}

        data = self._user_data_store[user_id]
        logger.info(
            f"[DiscordUserData] Access for {user_id}. Current keys: {list(data.keys())}"
        )
        return data

    def on_callback_query(self, pattern: str, handler: Callable[[UnifiedContext], Any]):
        """Register a callback query handler with regex pattern"""
        import re

        self._callback_handlers.append((re.compile(pattern), handler))
        logger.info(f"Registered Discord callback handler for pattern: {pattern}")
