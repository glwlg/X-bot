from abc import ABC, abstractmethod
from typing import Any, Optional, Union
from .models import UnifiedContext, UnifiedMessage

class BotAdapter(ABC):
    """Abstract Base Class for Bot Platforms (Adapters)"""

    def __init__(self, platform_name: str):
        self.platform_name = platform_name

    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (e.g., start polling or webhook)"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter"""
        pass

    @abstractmethod
    async def reply_text(self, context: UnifiedContext, text: str, **kwargs) -> Any:
        """Reply with text"""
        pass

    @abstractmethod
    async def edit_text(self, context: UnifiedContext, message_id: str, text: str, **kwargs) -> Any:
        """Edit a text message. Should handle fallback if not supported."""
        pass

    @abstractmethod
    async def reply_photo(self, context: UnifiedContext, photo: Union[str, bytes], caption: Optional[str] = None, **kwargs) -> Any:
        """Reply with a photo"""
        pass

    @abstractmethod
    async def delete_message(self, context: UnifiedContext, message_id: str, chat_id: Optional[str] = None, **kwargs) -> Any:
        """Delete a message"""
        pass

    @abstractmethod
    async def send_chat_action(self, context: UnifiedContext, action: str, chat_id: Optional[str] = None, **kwargs) -> Any:
        """Send a chat action"""
        pass

    @abstractmethod
    async def download_file(self, context: UnifiedContext, file_id: str, **kwargs) -> bytes:
        """Download file content as bytes"""
        pass
