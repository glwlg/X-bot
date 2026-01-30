from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union
from enum import Enum
from datetime import datetime

class MessageType(Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"
    LOCATION = "location"
    CONTACT = "contact"
    STICKER = "sticker"
    ANIMATION = "animation"
    UNKNOWN = "unknown"

@dataclass
class User:
    """Unified User Model"""
    id: str  # Platform-specific ID (e.g., "tg_123456", "dc_987654")
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: Optional[str] = None
    is_bot: bool = False
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.username or "Unknown"

@dataclass
class Chat:
    """Unified Chat Model"""
    id: str
    type: str # "private", "group", "supergroup", "channel"
    title: Optional[str] = None
    username: Optional[str] = None

@dataclass
class UnifiedMessage:
    """Unified Message Model"""
    id: str
    platform: str  # "telegram", "discord", "dingtalk"
    user: User
    chat: Chat
    date: datetime
    type: MessageType
    text: Optional[str] = None
    caption: Optional[str] = None
    file_id: Optional[str] = None # Platform-specific file ID
    file_url: Optional[str] = None # Direct download URL if available
    reply_to_message_id: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def content(self) -> str:
        """Returns text content or caption"""
        return self.text or self.caption or ""

class PlatformContext(Protocol):
    """Protocol for platform-specific context operations"""
    
    async def reply_text(self, text: str, **kwargs) -> Any:
        ...
        
    async def reply_image(self, image: Union[str, bytes], caption: Optional[str] = None, **kwargs) -> Any:
        ...

    async def edit_text(self, message_id: str, text: str, **kwargs) -> Any:
        ...

    async def delete_message(self, message_id: str, chat_id: Optional[str] = None, **kwargs) -> Any:
        ...

    async def send_chat_action(self, action: str, chat_id: Optional[str] = None, **kwargs) -> Any:
        ...

    async def download_file(self, file_id: str, **kwargs) -> bytes:
        ...

@dataclass
class UnifiedContext:
    """Unified Context passed to handlers"""
    message: UnifiedMessage
    platform_ctx: Any # Original platform context (e.g., telegram.ext.ContextTypes.DEFAULT_TYPE)
    platform_event: Any = None # Original platform event (e.g., telegram.Update) - Escape hatch
    _adapter: Any = None # Reference to the adapter instance
    
    async def reply(self, text: str, **kwargs) -> Any:
        """
        Unified reply method.
        Supports HTML formatting by default (adapters should handle conversion).
        """
        return await self._adapter.reply_text(self, text, **kwargs)

    async def edit_message(self, message_id: str, text: str, **kwargs) -> Any:
        """
        Unified edit method.
        If platform doesn't support editing, it should fallback to sending a new message.
        """
        return await self._adapter.edit_text(self, message_id, text, **kwargs)
    
    async def reply_photo(self, photo: Union[str, bytes], caption: Optional[str] = None, **kwargs) -> Any:
        return await self._adapter.reply_photo(self, photo, caption, **kwargs)

    async def reply_video(self, video: Union[str, bytes], caption: Optional[str] = None, **kwargs) -> Any:
        return await self._adapter.reply_video(self, video, caption, **kwargs)

    async def reply_document(self, document: Union[str, bytes], filename: Optional[str] = None, caption: Optional[str] = None, **kwargs) -> Any:
        return await self._adapter.reply_document(self, document, filename, caption, **kwargs)

    async def reply_audio(self, audio: Union[str, bytes], caption: Optional[str] = None, **kwargs) -> Any:
        return await self._adapter.reply_audio(self, audio, caption, **kwargs)

    async def delete_message(self, message_id: str, chat_id: Optional[str] = None, **kwargs) -> Any:
        """
        Delete a message.
        """
        return await self._adapter.delete_message(self, message_id, chat_id, **kwargs)

    async def send_chat_action(self, action: str, chat_id: Optional[str] = None, **kwargs) -> Any:
        """
        Send a chat action (typing, etc).
        """
        return await self._adapter.send_chat_action(self, action, chat_id, **kwargs)

    async def download_file(self, file_id: str, **kwargs) -> bytes:
        """
        Download a file by ID and return bytes.
        """
        return await self._adapter.download_file(self, file_id, **kwargs)
