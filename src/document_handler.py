"""
文档分析模块 - 支持 PDF 和 Word 文档的内容提取和分析
"""
import io
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from config import gemini_client, GEMINI_MODEL, is_user_allowed

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


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理文档消息，提取内容并使用 AI 分析
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    if not await is_user_allowed(user_id):
        await update.message.reply_text(
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取文档
    document = update.message.document
    if not document:
        return
    
    # 检查文件类型
    mime_type = document.mime_type
    if mime_type not in SUPPORTED_MIME_TYPES:
        await update.message.reply_text(
            "⚠️ 不支持的文档格式。\n\n"
            "支持的格式：PDF、DOCX"
        )
        return
    
    # 检查文件大小（限制 10MB）
    if document.file_size and document.file_size > 10 * 1024 * 1024:
        await update.message.reply_text(
            "⚠️ 文档过大（超过 10MB），请发送较小的文档。"
        )
        return
    
    # 获取用户问题（如果有）
    caption = update.message.caption or "请分析这个文档的主要内容"
    
    # 发送处理中提示
    thinking_msg = await update.message.reply_text("📄 正在读取文档内容...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # 下载文档
        file = await context.bot.get_file(document.file_id)
        file_bytes = bytes(await file.download_as_bytearray())
        
        # 根据类型提取文本
        doc_type = SUPPORTED_MIME_TYPES[mime_type]
        if doc_type == "pdf":
            text = extract_text_from_pdf(file_bytes)
        elif doc_type in ["docx", "doc"]:
            text = extract_text_from_docx(file_bytes)
        else:
            text = ""
        
        if not text or len(text.strip()) < 50:
            await thinking_msg.edit_text(
                "❌ 无法提取文档内容。\n\n"
                "可能的原因：\n"
                "• 文档是扫描版（图片）\n"
                "• 文档被加密保护\n"
                "• 文档格式损坏"
            )
            return
        
        # 限制文本长度
        max_length = 15000
        if len(text) > max_length:
            text = text[:max_length] + "\n\n[内容过长，已截断...]"
        
        await thinking_msg.edit_text("📄 正在分析文档内容...")
        
        # 调用 Gemini 分析
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
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
        
        if response.text:
            await thinking_msg.edit_text(response.text)
            # 记录统计
            from stats import increment_stat
            increment_stat(user_id, "ai_chats")
        else:
            await thinking_msg.edit_text("抱歉，我无法分析这个文档。请稍后再试。")
        
    except Exception as e:
        logger.error(f"Document processing error: {e}")
        try:
            await thinking_msg.edit_text(
                "❌ 文档处理失败，请稍后再试。\n\n"
                "可能的原因：\n"
                "• 文档格式不支持\n"
                "• 文档内容无法解析\n"
                "• 服务暂时不可用"
            )
        except BadRequest:
            pass
