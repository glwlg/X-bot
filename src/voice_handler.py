"""
语音消息处理模块 - 使用 Gemini 分析语音内容
"""
import logging
import base64
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from config import gemini_client, GEMINI_MODEL, is_user_allowed

logger = logging.getLogger(__name__)


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理语音消息，使用 Gemini AI 转写并回复
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    if not await is_user_allowed(user_id):
        await update.message.reply_text(
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取语音消息
    voice = update.message.voice
    if not voice:
        return
    
    # 检查时长（限制 60 秒）
    if voice.duration > 60:
        await update.message.reply_text(
            "⚠️ 语音消息过长（超过 60 秒），请发送较短的语音。"
        )
        return
    
    # 发送处理中提示
    thinking_msg = await update.message.reply_text("🎤 正在识别语音内容...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # 下载语音文件
        file = await context.bot.get_file(voice.file_id)
        voice_bytes = await file.download_as_bytearray()
        
        # 获取 MIME 类型
        mime_type = voice.mime_type or "audio/ogg"
        
        # 构建请求内容
        contents = [
            {
                "parts": [
                    {"text": "请听这段语音，转写其中的文字内容，然后根据内容进行回复。如果无法识别，请说明原因。"},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(voice_bytes)).decode("utf-8"),
                        }
                    },
                ]
            }
        ]
        
        # 调用 Gemini API
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "system_instruction": (
                    "你是一个友好的助手，可以理解语音内容并进行对话。"
                    "请先转写语音中的文字，然后针对内容进行回复。"
                    "请用中文回复。"
                ),
            },
        )
        
        if response.text:
            await thinking_msg.edit_text(response.text)
            # 记录统计
            from stats import increment_stat
            await increment_stat(user_id, "voice_chats")
        else:
            await thinking_msg.edit_text("抱歉，我无法识别这段语音。请稍后再试。")
        
    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        try:
            await thinking_msg.edit_text(
                "❌ 语音处理失败，请稍后再试。\n\n"
                "可能的原因：\n"
                "• 语音格式不支持\n"
                "• 语音内容无法识别\n"
                "• 服务暂时不可用"
            )
        except BadRequest:
            pass
