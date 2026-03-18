"""
文档分析模块 - 支持 PDF 和 Word 文档的内容提取和分析
"""

import io
import logging
from typing import Any
from telegram.error import BadRequest

from core.config import is_user_allowed, get_client_for_model
from core.model_config import get_current_model
from core.platform.exceptions import MediaProcessingError
from services.openai_adapter import generate_text
from user_context import add_message, bind_delivery_target
from core.platform.models import UnifiedContext, MessageType
from .ai_handlers import _acknowledge_received
from .media_utils import extract_media_input

logger = logging.getLogger(__name__)

# 支持的文档类型
SUPPORTED_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
}


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """从 PDF 文件提取文本"""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except Exception as e:
        logger.error(f"Failed to extract text from PDF: {e}")
        return ""


def extract_text_from_docx(file_bytes: bytes) -> str:
    """从 DOCX 文件提取文本"""
    try:
        from docx import Document

        doc = Document(io.BytesIO(file_bytes))
        text = "\n".join([para.text for para in doc.paragraphs if para.text])
        return text
    except Exception as e:
        logger.error(f"Failed to extract text from DOCX: {e}")
        return ""


def get_message_id(msg: Any) -> str:
    """
    统一获取消息 ID（跨平台兼容）
    - Telegram: msg.message_id
    - Discord: msg.id
    """
    if hasattr(msg, "message_id"):
        return str(msg.message_id)
    elif hasattr(msg, "id"):
        return str(msg.id)
    else:
        return str(msg)


async def handle_document(ctx: UnifiedContext) -> None:
    """
    处理文档消息，提取内容并使用 AI 分析
    """
    user_id = ctx.message.user.id

    # 检查用户权限
    if not await is_user_allowed(user_id):
        return

    await _acknowledge_received(ctx)

    # 检查消息类型
    if ctx.message.type != MessageType.DOCUMENT:
        return

    try:
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.DOCUMENT},
            auto_download=True,
        )
    except MediaProcessingError as exc:
        if exc.error_code == "unsupported_media_on_platform":
            await ctx.reply("❌ 当前平台暂不支持该文档消息格式。")
        else:
            await ctx.reply("❌ 当前平台暂时无法下载文档内容，请稍后重试。")
        return

    file_name = media.file_name
    mime_type = media.mime_type
    file_size = media.file_size
    file_bytes = media.content or b""

    if not file_bytes:
        await ctx.reply("❌ 无法获取文档数据，请重新发送。")
        return

    # 获取用户问题（如果有）
    caption = ctx.message.caption or ""

    # 检查文件类型
    # mime_type already extracted above
    if mime_type not in SUPPORTED_MIME_TYPES:
        await ctx.reply("⚠️ 不支持的文档格式。\n\n支持的格式：PDF、DOCX")
        return

    # 检查文件大小（限制 10MB）
    if file_size and file_size > 10 * 1024 * 1024:
        await ctx.reply("⚠️ 文档过大（超过 10MB），请发送较小的文档。")
        return

    # 获取用户问题（如果有）
    caption = ctx.message.caption or "请分析这个文档的主要内容"

    # 发送处理中提示
    thinking_msg = await ctx.reply("📄 正在读取文档内容...")

    # 记录用户文档消息到上下文
    await bind_delivery_target(ctx, user_id)
    await add_message(
        ctx,
        user_id,
        "user",
        f"【用户发送了文档：{file_name or 'document'}】{caption}",
    )

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    try:
        # 根据类型提取文本
        doc_type = SUPPORTED_MIME_TYPES[mime_type]
        if doc_type == "pdf":
            text = extract_text_from_pdf(file_bytes)
        elif doc_type in ["docx", "doc"]:
            text = extract_text_from_docx(file_bytes)
        else:
            text = ""

        if not text or len(text.strip()) < 50:
            await ctx.edit_message(
                get_message_id(thinking_msg),
                "❌ 无法提取文档内容。\n\n"
                "可能的原因：\n"
                "• 文档是扫描版（图片）\n"
                "• 文档被加密保护\n"
                "• 文档格式损坏",
            )
            return

        # 限制文本长度
        max_length = 15000
        if len(text) > max_length:
            text = text[:max_length] + "\n\n[内容过长，已截断...]"

        await ctx.edit_message(get_message_id(thinking_msg), "📄 正在分析文档内容...")

        model_to_use = get_current_model()
        client_to_use = get_client_for_model(model_to_use, is_async=True)
        if client_to_use is None:
            raise RuntimeError("OpenAI async client is not initialized")
        response_text = await generate_text(
            async_client=client_to_use,
            model=model_to_use,
            contents=f"用户问题：{caption}\n\n文档内容：\n{text}",
            config={
                "system_instruction": (
                    "你是一个专业的文档分析助手。"
                    "请根据用户的问题分析文档内容。"
                    "如果用户没有具体问题，请总结文档的主要内容。"
                    "请用中文回复。"
                ),
            },
        )
        response_text = str(response_text or "")

        if response_text:
            await ctx.edit_message(get_message_id(thinking_msg), response_text)
            # 记录模型回复到上下文
            await add_message(ctx, user_id, "model", response_text)
            # 记录统计
            from stats import increment_stat

            await increment_stat(user_id, "doc_analyses")
        else:
            await ctx.edit_message(
                get_message_id(thinking_msg), "抱歉，我无法分析这个文档。请稍后再试。"
            )

    except Exception as e:
        logger.error(f"Document processing error: {e}")
        try:
            await ctx.edit_message(
                get_message_id(thinking_msg),
                "❌ 文档处理失败，请稍后再试。\n\n"
                "可能的原因：\n"
                "• 文档格式不支持\n"
                "• 文档内容无法解析\n"
                "• 服务暂时不可用",
            )
        except BadRequest:
            pass
