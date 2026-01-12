"""
语音消息处理模块 - 智能路由版

短语音（≤60s）: 转文字后走智能路由（与文本消息一致）
长语音（>60s）: 直接转写输出
"""
import logging
import base64
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from config import gemini_client, GEMINI_MODEL, is_user_allowed
from user_context import add_message, get_user_context
from utils import smart_edit_text, smart_reply_text

logger = logging.getLogger(__name__)

# 语音时长阈值（秒）
SHORT_VOICE_THRESHOLD = 60


async def transcribe_voice(voice_bytes: bytes, mime_type: str) -> str | None:
    """
    使用 Gemini 转写语音为文字
    
    Returns:
        转写后的文本，失败返回 None
    """
    try:
        contents = [
            {
                "parts": [
                    {"text": "请将这段语音转写为文字。只输出语音中说的原话，不要添加任何解释或回复。如果无法识别，返回空字符串。"},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(voice_bytes)).decode("utf-8"),
                        }
                    },
                ]
            }
        ]
        
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
        )
        
        if response.text and len(response.text.strip()) > 0:
            return response.text.strip()
        return None
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        return None


async def transcribe_and_translate_voice(voice_bytes: bytes, mime_type: str) -> dict | None:
    """
    转写语音并翻译为双语对照
    
    Returns:
        {"original": "原文", "original_lang": "语言", "translated": "译文"} 或 None
    """
    try:
        prompt = (
            "请完成以下任务：\n"
            "1. 将语音转写为文字\n"
            "2. 识别语音的语言\n"
            "3. 如果是中文，翻译为英文；如果是其他语言，翻译为中文\n\n"
            "请严格按以下格式输出（不要添加其他内容）：\n"
            "原文语言：[语言名称]\n"
            "原文：[转写的原文]\n"
            "译文：[翻译后的文字]"
        )
        
        contents = [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(voice_bytes)).decode("utf-8"),
                        }
                    },
                ]
            }
        ]
        
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
        )
        
        if not response.text:
            return None
        
        # 解析结果
        text = response.text.strip()
        result = {}
        
        for line in text.split('\n'):
            if line.startswith('原文语言：'):
                result['original_lang'] = line.replace('原文语言：', '').strip()
            elif line.startswith('原文：'):
                result['original'] = line.replace('原文：', '').strip()
            elif line.startswith('译文：'):
                result['translated'] = line.replace('译文：', '').strip()
        
        if result.get('original') and result.get('translated'):
            return result
        return None
        
    except Exception as e:
        logger.error(f"Voice translation error: {e}")
        return None


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理语音消息
    
    翻译模式开启: 转写 + 翻译 → 双语对照输出
    正常模式:
        短语音: 转文字 → 智能路由
        长语音: 直接转写输出
    """
    from database import get_user_settings
    
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    if not await is_user_allowed(user_id):
        await smart_reply_text(update, "⛔ 抱歉，您没有使用 AI 功能的权限。")
        return
    
    # 获取语音消息
    voice = update.message.voice
    if not voice:
        return
    
    # 检查是否开启翻译模式
    settings = await get_user_settings(user_id)
    translate_mode = settings.get("auto_translate", 0)
    
    # 发送处理中提示
    if translate_mode:
        thinking_msg = await smart_reply_text(update, "🌍 正在翻译语音内容...")
    else:
        thinking_msg = await smart_reply_text(update, "🎤 正在识别语音内容...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # 下载语音文件
        file = await context.bot.get_file(voice.file_id)
        voice_bytes = await file.download_as_bytearray()
        mime_type = voice.mime_type or "audio/ogg"
        
        # 翻译模式：双语对照输出
        if translate_mode:
            result = await transcribe_and_translate_voice(voice_bytes, mime_type)
            
            if not result:
                await smart_edit_text(thinking_msg, "❌ 无法识别或翻译语音内容，请重试。")
                return
            
            original_lang = result.get('original_lang', '未知')
            original = result.get('original', '')
            translated = result.get('translated', '')
            
            output = (
                f"🎤 **语音翻译**\n\n"
                f"📝 **原文** ({original_lang}):\n"
                f"「{original}」\n\n"
                f"🌐 **译文**:\n"
                f"「{translated}」"
            )
            
            await smart_edit_text(thinking_msg, output)
            
            # 记录统计
            from stats import increment_stat
            await increment_stat(user_id, "translations_count")
            return
        
        # 正常模式：转写语音
        transcribed_text = await transcribe_voice(voice_bytes, mime_type)
        
        if not transcribed_text:
            await smart_edit_text(thinking_msg, "❌ 无法识别语音内容，请重试或发送文字消息。")
            return
        
        logger.info(f"Voice transcribed: {transcribed_text[:50]}...")
        
        # 根据语音时长决定处理策略
        if voice.duration <= SHORT_VOICE_THRESHOLD:
            # 短语音：走智能路由（与文本消息一致）
            await smart_edit_text(thinking_msg, f"🎤 语音转写内容为: **\"{transcribed_text}\"**\n\n🤔 正在思考中...")
            
            # 调用文本消息处理逻辑
            await process_as_text_message(update, context, transcribed_text, thinking_msg)
        else:
            # 长语音：直接输出转写结果
            await smart_edit_text(thinking_msg, f"🎤 **语音转写结果：**\n\n{transcribed_text}")
            
            # 记录到上下文
            add_message(context, "user", f"【用户发送了一段长语音】{transcribed_text}")
            
            # 记录统计
            from stats import increment_stat
            await increment_stat(user_id, "voice_chats")
        
    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        try:
            await smart_edit_text(thinking_msg,
                "❌ 语音处理失败，请稍后再试。\n\n"
                "可能的原因：\n"
                "• 语音格式不支持\n"
                "• 语音内容无法识别\n"
                "• 服务暂时不可用"
            )
        except BadRequest:
            pass


async def process_as_text_message(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE, 
    text: str,
    thinking_msg
) -> None:
    """
    将转写后的文本按普通文本消息逻辑处理（智能路由）
    """
    import time
    from intent_router import analyze_intent, UserIntent
    from handlers.ai_handlers import handle_ai_chat
    from stats import increment_stat
    
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    
    # 记录用户消息到上下文
    add_message(context, "user", text)
    
    # 分析意图
    intent_result = await analyze_intent(text)
    intent = intent_result.get("intent")
    params = intent_result.get("params", {})
    
    logger.info(f"Voice Smart Routing: {intent} | params={params}")
    
    # 处理特殊意图
    if intent == UserIntent.DOWNLOAD_VIDEO:
        from web_summary import extract_urls
        from handlers.media_handlers import process_video_download
        
        target_url = params.get("url")
        if not target_url:
            found_urls = extract_urls(text)
            if found_urls:
                target_url = found_urls[0]
        
        if target_url:
            await smart_edit_text(thinking_msg, f"🚀 识别到下载意图，正在处理链接...")
            await process_video_download(update, context, target_url, audio_only=False)
            return
    
    elif intent == UserIntent.GENERATE_IMAGE:
        prompt = params.get("prompt") or text
        await smart_edit_text(thinking_msg, f"🎨 识别到画图意图，正在生成...")
        from image_generator import handle_image_generation
        await handle_image_generation(update, context, prompt)
        return
    
    elif intent == UserIntent.SET_REMINDER:
        time_str = params.get("time")
        content = params.get("content")
        if time_str and content:
            from handlers.service_handlers import process_remind
            await smart_edit_text(thinking_msg, f"⏰ 识别到提醒意图，正在设置...")
            await process_remind(update, context, time_str, content)
            return
    
    elif intent == UserIntent.RSS_SUBSCRIBE:
        url = params.get("url")
        if url:
            from handlers.service_handlers import process_subscribe
            await smart_edit_text(thinking_msg, f"📢 识别到订阅意图，正在处理...")
            await process_subscribe(update, context, url)
            return
    
    elif intent == UserIntent.MONITOR_KEYWORD:
        keyword = params.get("keyword")
        if keyword:
            from handlers.service_handlers import process_monitor
            await smart_edit_text(thinking_msg, f"🔍 识别到监控意图，正在处理...")
            await process_monitor(update, context, keyword)
            return
    
    # 普通对话：走 AI 生成流程
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # 构建上下文
    context_messages = get_user_context(context)
    context_messages.append({
        "role": "user",
        "parts": [{"text": text}]
    })
    
    # 生成回复
    from services.ai_service import AiService
    ai_service = AiService()
    
    enable_memory = (intent == UserIntent.MEMORY_RECALL)
    if enable_memory:
        logger.info(f"Memory tools enabled for voice intent: {intent}")
    
    final_text_response = ""
    last_update_time = 0
    
    async for chunk_text in ai_service.generate_response_stream(user_id, context_messages, enable_memory=enable_memory):
        final_text_response += chunk_text
        
        now = time.time()
        if now - last_update_time > 0.8:
            await smart_edit_text(thinking_msg, final_text_response)
            last_update_time = now
    
    # 发送最终回复
    if final_text_response:
        await smart_edit_text(thinking_msg, final_text_response)
        add_message(context, "model", final_text_response)
        await increment_stat(user_id, "voice_chats")
    else:
        await smart_edit_text(thinking_msg, "抱歉，我无法生成回复。请稍后再试。")
