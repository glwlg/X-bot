import time
import logging
import base64
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from core.config import gemini_client, GEMINI_MODEL
from services.web_summary_service import extract_urls, summarize_webpage, is_video_platform, fetch_webpage_content
from user_context import get_user_context, add_message
from repositories import get_user_settings, get_video_cache
from utils import smart_edit_text, smart_reply_text
from stats import increment_stat

logger = logging.getLogger(__name__)

# 思考提示消息
THINKING_MESSAGE = "🤔 正在思考中..."


async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理普通文本消息，使用 Gemini AI 生成回复
    支持引用（回复）包含图片或视频的消息
    """
    user_message = update.message.text
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not user_message:
        return

    # 检查用户权限
    from core.config import is_user_allowed
    if not await is_user_allowed(user_id):
        await smart_reply_text(update,
            "⛔ 抱歉，您没有使用 AI 对话功能的权限。\n\n"
            "如需下载视频，请使用 /download 命令。"
        )
        return

    # 检查消息中是否包含 URL（自动生成网页摘要）
    urls = extract_urls(user_message)
    
    # 如果只是一个 URL 且没有其他内容
    if urls and user_message.strip() in urls:
        url = urls[0]
        
        # 智能逻辑：如果是视频平台链接，询问用户意图
        if is_video_platform(url):
            context.user_data['pending_video_url'] = url
            
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [
                [
                    InlineKeyboardButton("📹 下载视频", callback_data="action_download_video"),
                    InlineKeyboardButton("📝 AI 摘要", callback_data="action_summarize_video"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await smart_reply_text(update,
                "🤔 检测到视频链接，您想要做什么？",
                reply_markup=reply_markup
            )
            return

        # 普通网页，直接生成摘要
        thinking_msg = await smart_reply_text(update, "📄 正在获取网页内容并生成摘要...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        summary = await summarize_webpage(url)
        # Use smart_edit_text which handles Markdown conversion and fallbacks
        await smart_edit_text(thinking_msg, summary)
        
        # 记录统计
        await increment_stat(user_id, "ai_chats")
        return

    # 检查是否开启了沉浸式翻译
    settings = await get_user_settings(user_id)
    if settings.get("auto_translate", 0):
        # 翻译模式开启
        thinking_msg = await smart_reply_text(update, "🌍 翻译中...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config={
                    "system_instruction": (
                        "你是一个专业的翻译助手。请根据以下规则进行翻译：\n"
                        "1. 如果输入是中文，请翻译成英文。\n"
                        "2. 如果输入是其他语言，请翻译成简体中文。\n"
                        "3. 只输出译文，不要包含任何解释或额外的文本。\n"
                        "4. 保持原文的语气和格式。"
                    ),
                },
            )
            if response.text:
                await smart_edit_text(thinking_msg, f"🌍 **译文**\n\n{response.text}")
                # 统计
                await increment_stat(user_id, "translations_count")
            if response.text:
                await smart_edit_text(thinking_msg, f"🌍 **译文**\n\n{response.text}")
                # 统计
                await increment_stat(user_id, "translations_count")
            else:
                await smart_edit_text(thinking_msg, "❌ 无法翻译。")
        except Exception as e:
            logger.error(f"Translation error: {e}")
            await smart_edit_text(thinking_msg, "❌ 翻译服务出错。")
        return

    # --- Agent Orchestration ---
    from core.agent_orchestrator import agent_orchestrator
    
    # 1. 检查是否引用了消息 (Reply Context)
    from .message_utils import process_reply_message, process_and_send_code_files
    
    extra_context = "" 
    has_media, reply_extra_context, media_data, mime_type = await process_reply_message(update, context)
    
    if reply_extra_context:
        extra_context += reply_extra_context
    
    # Check if we should abort (e.g. file too big)
    if update.message.reply_to_message:
         r = update.message.reply_to_message
         if (r.video or r.audio or r.voice) and not has_media:
             return
    
    # 2. 检查当前消息中是否有 URL (混合文本情况)
    # 如果 extra_context 为空，且 urls 不为空，说明可能是 "Look at this https://..."
    if not extra_context and urls:
        status_msg = await smart_reply_text(update, "📄 正在获取网页内容...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            web_content = await fetch_webpage_content(urls[0])
            if web_content:
                extra_context = f"【网页内容】\n{web_content}\n\n"
            else:
                extra_context = "【系统提示】检测到链接，无法读取详情。\n\n"
            
        except Exception as e:
            logger.error(f"Error fetching mixed URL: {e}")
        
        try:
            await status_msg.delete()
        except:
            pass

    if not has_media:
        thinking_msg = await smart_reply_text(update, THINKING_MESSAGE)
    else:
        thinking_msg = await smart_reply_text(update, "🤔 正在分析引用内容...")
    
    # 3. 构建消息上下文 (History)
    # 将网页上下文合并到用户消息中
    final_user_message = user_message
    if extra_context:
        final_user_message = extra_context + "用户请求：" + user_message

    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # A. 带媒体的请求 (Gemini Vision) - 暂时不走 Agent Loop (Vision model function calling support is limited/tricky)
        # 或者我们把 Vision 也做成 Agent 的输入？
        # 目前 Gemini 2.0 Flash 支持多模态 + Tools。
        # 让我们尝试把 Media 放入 history 传给 Agent！
        
        message_history = []
        
        # 构建当前消息
        current_msg_parts = []
        current_msg_parts.append({"text": final_user_message})
        
        if has_media and media_data:
             current_msg_parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(bytes(media_data)).decode("utf-8"),
                }
            })
            
        # 获取历史上下文
        history = get_user_context(context) # Returns list of dicts
        
        # 拼接: History + Current
        message_history.extend(history)
        message_history.append({
            "role": "user",
            "parts": current_msg_parts
        })
        
        # B. 调用 Agent Orchestrator
        final_text_response = ""
        last_update_time = 0
        
        async for chunk_text in agent_orchestrator.handle_message(update, context, message_history):
            final_text_response += chunk_text
            
            # Update UI
            now = time.time()
            if now - last_update_time > 0.8:
                await smart_edit_text(thinking_msg, final_text_response)
                last_update_time = now

        # 5. 发送最终回复并入库
        if final_text_response:
            sent_msg = await smart_edit_text(thinking_msg, final_text_response)
            
            # 记录模型回复到上下文
            add_message(context, "model", final_text_response)
            
            # Try to extract code blocks
            final_display_text = await process_and_send_code_files(update, context, final_text_response)
            
            if sent_msg and final_display_text != final_text_response:
                 await smart_edit_text(sent_msg, final_display_text)

            # 记录统计
            await increment_stat(user_id, "ai_chats")
        else:
            await smart_edit_text(thinking_msg, "抱歉，我无法生成回复 (无输出)。")

    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        await smart_edit_text(thinking_msg,
            f"❌ Agent 运行出错：{e}\n\n请尝试 /new 重置对话。"
        )


async def handle_ai_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理图片消息，使用 Gemini AI 分析图片
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    from core.config import is_user_allowed
    if not await is_user_allowed(user_id):
        await smart_reply_text(update,
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取图片（选择最大分辨率）
    photo = update.message.photo[-1]
    caption = update.message.caption or "请描述这张图片"

    # Save to history immediately
    add_message(context, "user", f"【用户发送了一张图片】 {caption}")
    
    # 立即发送"正在分析"提示
    thinking_msg = await smart_reply_text(update, "🔍 正在分析图片...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # 下载图片
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        
        # 构建带图片的内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(bytes(image_bytes)).decode("utf-8"),
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
                "system_instruction": "你是一个友好的助手，可以分析图片并回答问题。请用中文回复。",
            },
        )
        
        if response.text:
            # Try to extract code blocks, send files, and get cleaned text
            from .message_utils import process_and_send_code_files
            display_text = await process_and_send_code_files(update, context, response.text)
            
            # 更新消息
            await smart_edit_text(thinking_msg, display_text)
            
            # Save model response to history
            add_message(context, "model", response.text)
            
            # 记录统计
            await increment_stat(user_id, "photo_analyses")

        else:
            await smart_edit_text(thinking_msg, "抱歉，我无法分析这张图片。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI photo analysis error: {e}")
        await smart_edit_text(thinking_msg, "❌ 图片分析失败，请稍后再试。")


async def handle_ai_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理视频消息，使用 Gemini AI 分析视频
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    from core.config import is_user_allowed
    if not await is_user_allowed(user_id):
        await smart_reply_text(update,
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取视频
    video = update.message.video
    if not video:
        return
    
    caption = update.message.caption or "请分析这个视频的内容"
    
    # 检查视频大小（Gemini 有限制）
    # 检查视频大小（Gemini 有限制）
    if video.file_size and video.file_size > 20 * 1024 * 1024:  # 20MB 限制
        await smart_reply_text(update,
            "⚠️ 视频文件过大（超过 20MB），无法分析。\n\n"
            "请尝试发送较短的视频片段。"
        )
        return
    
    # 立即发送"正在分析"提示
    # 立即发送"正在分析"提示
    thinking_msg = await smart_reply_text(update, "🎬 正在分析视频，这可能需要一些时间...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # 下载视频
        file = await context.bot.get_file(video.file_id)
        video_bytes = await file.download_as_bytearray()
        
        # 获取 MIME 类型
        mime_type = video.mime_type or "video/mp4"
        
        # 构建带视频的内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(video_bytes)).decode("utf-8"),
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
                "system_instruction": "你是一个友好的助手，可以分析视频内容并回答问题。请用中文回复。",
            },
        )
        
        if response.text:
            # Try to extract code blocks, send files, and get cleaned text
            from .message_utils import process_and_send_code_files
            display_text = await process_and_send_code_files(update, context, response.text)
            
            # Update the thinking message with the cleaned text
            await smart_edit_text(thinking_msg, display_text)
            
            # 记录统计
            await increment_stat(user_id, "video_analyses")
        else:
            await smart_edit_text(thinking_msg, "抱歉，我无法分析这个视频。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI video analysis error: {e}")
        await smart_edit_text(thinking_msg,
            "❌ 视频分析失败，请稍后再试。\n\n"
            "可能的原因：\n"
            "• 视频格式不支持\n"
            "• 视频时长过长\n"
            "• 服务暂时不可用"
        )
