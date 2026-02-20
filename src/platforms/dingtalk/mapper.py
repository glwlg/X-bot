"""
DingTalk Message Mapper

将钉钉的 ChatbotMessage 转换为 UnifiedMessage
"""

from datetime import datetime
from typing import Optional
from core.platform.models import UnifiedMessage, User, Chat, MessageType


def map_dingtalk_message(
    msg_data: dict,
    conversation_id: str,
    sender_id: str,
    sender_nick: str,
    conversation_type: str = "1",
) -> UnifiedMessage:
    """
    Map DingTalk ChatbotMessage data to UnifiedMessage.

    Args:
        msg_data: The message data dict from callback
        conversation_id: DingTalk conversation ID
        sender_id: Sender's staffId or unionId
        sender_nick: Sender's display name
        conversation_type: "1" for single chat, "2" for group chat

    Returns:
        UnifiedMessage object
    """
    # 解析消息类型
    msg_type_str = msg_data.get("msgtype", "text")
    msg_type = MessageType.TEXT
    text_content = ""
    file_id = None
    file_url = None

    if msg_type_str == "text":
        msg_type = MessageType.TEXT
        text_content = msg_data.get("text", {}).get("content", "").strip()
    elif msg_type_str == "picture":
        msg_type = MessageType.IMAGE
        picture_url = msg_data.get("content", {}).get("pictureDownloadCode")
        file_url = picture_url
    elif msg_type_str == "richText":
        msg_type = MessageType.TEXT
        # richText 包含多个段落，提取文本
        rich_text = msg_data.get("content", {}).get("richText", [])
        text_parts = []
        for item in rich_text:
            if "text" in item:
                text_parts.append(item["text"])
        text_content = "\n".join(text_parts)
    elif msg_type_str == "video":
        msg_type = MessageType.VIDEO
        file_url = msg_data.get("content", {}).get("downloadCode")
    elif msg_type_str == "audio":
        msg_type = MessageType.AUDIO
        file_url = msg_data.get("content", {}).get("downloadCode")
    elif msg_type_str == "file":
        msg_type = MessageType.DOCUMENT
        file_url = msg_data.get("content", {}).get("downloadCode")

    # 构建 User
    unified_user = User(
        id=str(sender_id),
        username=sender_nick,
        first_name=sender_nick,
        is_bot=False,
    )

    # 构建 Chat
    # conversation_type: "1" = 单聊, "2" = 群聊
    chat_type = "private" if conversation_type == "1" else "group"
    unified_chat = Chat(
        id=str(conversation_id),
        type=chat_type,
        title=None,  # 钉钉群名需要额外 API 获取
    )

    # 生成消息 ID (使用时间戳，钉钉没有原生 message_id)
    message_id = msg_data.get("msgId", str(int(datetime.now().timestamp() * 1000)))

    return UnifiedMessage(
        id=str(message_id),
        platform="dingtalk",
        user=unified_user,
        chat=unified_chat,
        date=datetime.now(),
        type=msg_type,
        text=text_content,
        file_id=file_id,
        file_url=file_url,
    )


def map_chatbot_message(incoming_message) -> UnifiedMessage:
    """
    Map dingtalk_stream.ChatbotMessage to UnifiedMessage.

    Args:
        incoming_message: ChatbotMessage object from dingtalk_stream SDK

    Returns:
        UnifiedMessage object
    """
    # ChatbotMessage 属性:
    # - sender_id: 发送者 ID
    # - sender_nick: 发送者昵称
    # - sender_staff_id: 员工 ID (企业内部)
    # - conversation_id: 会话 ID
    # - conversation_type: "1" 单聊, "2" 群聊
    # - text: TextContent 对象 (有 content 属性)
    # - message_type: 消息类型
    # - msgtype: 消息类型字符串

    sender_id = getattr(incoming_message, "sender_staff_id", None) or getattr(
        incoming_message, "sender_id", "unknown"
    )
    sender_nick = getattr(incoming_message, "sender_nick", "Unknown")
    conversation_id = getattr(incoming_message, "conversation_id", "unknown")
    conversation_type = getattr(incoming_message, "conversation_type", "1")
    msg_id = getattr(incoming_message, "msg_id", None)

    # 解析消息内容
    msg_type = MessageType.TEXT
    text_content = ""

    # 获取文本内容
    text_obj = getattr(incoming_message, "text", None)
    if text_obj:
        text_content = getattr(text_obj, "content", "").strip()

    # 构建 User
    unified_user = User(
        id=str(sender_id),
        username=sender_nick,
        first_name=sender_nick,
        is_bot=False,
    )

    # 构建 Chat
    chat_type = "private" if str(conversation_type) == "1" else "group"
    unified_chat = Chat(
        id=str(conversation_id),
        type=chat_type,
    )

    # 消息 ID
    message_id = msg_id or str(int(datetime.now().timestamp() * 1000))

    return UnifiedMessage(
        id=str(message_id),
        platform="dingtalk",
        user=unified_user,
        chat=unified_chat,
        date=datetime.now(),
        type=msg_type,
        text=text_content,
    )
