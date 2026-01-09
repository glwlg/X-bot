"""
AI 对话处理模块 - 使用 Gemini API，支持文本、图片和视频
"""
import time
import logging
import base64
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from config import gemini_client, GEMINI_MODEL

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
    from config import is_user_allowed
    if not await is_user_allowed(user_id):
        await update.message.reply_text(
            "⛔ 抱歉，您没有使用 AI 对话功能的权限。\n\n"
            "如需下载视频，请使用 /download 命令。"
        )
        return

    # 检查消息中是否包含 URL（自动生成网页摘要）
    from web_summary import extract_urls, summarize_webpage, is_video_platform
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
            
            await update.message.reply_text(
                "🤔 检测到视频链接，您想要做什么？",
                reply_markup=reply_markup
            )
            return

        # 普通网页，直接生成摘要
        thinking_msg = await update.message.reply_text("📄 正在获取网页内容并生成摘要...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        summary = await summarize_webpage(url)
        try:
            await thinking_msg.edit_text(summary, parse_mode="Markdown")
        except BadRequest as e:
            # Fallback to plain text if Markdown parsing fails
            logger.warning(f"Markdown parsing failed for web summary: {e}, falling back to plain text.")
            await thinking_msg.edit_text(summary, parse_mode=None)
        
        # 记录统计
        from stats import increment_stat
        await increment_stat(user_id, "ai_chats")
        return

    # 检查是否开启了沉浸式翻译
    from database import get_user_settings
    settings = await get_user_settings(user_id)
    if settings.get("auto_translate", 0):
        # 翻译模式开启
        thinking_msg = await update.message.reply_text("🌍 翻译中...")
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
                await thinking_msg.edit_text(f"🌍 **译文**\n\n{response.text}", parse_mode="Markdown")
                # 统计
                from stats import increment_stat
                await increment_stat(user_id, "translations_count")
            else:
                await thinking_msg.edit_text("❌ 无法翻译。")
        except Exception as e:
            logger.error(f"Translation error: {e}")
            await thinking_msg.edit_text("❌ 翻译服务出错。")
        return

    # 检查是否引用了包含媒体的消息
    reply_to = update.message.reply_to_message
    has_media = False
    media_data = None
    mime_type = None
    extra_context = ""
    
    if reply_to:
        # 1. 尝试提取引用消息中的 URL 并获取内容
        reply_urls = []
        
        # DEBUG LOG
        logger.info(f"Checking reply_to message {reply_to.message_id} for URLs")
        
        # A. 从实体（超链接/文本链接）提取
        entities = reply_to.entities or reply_to.caption_entities or []
        for entity in entities:
            logger.info(f"Found entity: {entity.type} at offset {entity.offset}")
            if entity.type == "text_link":
                # Markdown/HTML 链接 [text](url)
                reply_urls.append(entity.url)
            elif entity.type == "url":
                # 纯文本 URL，需要从文本中截取
                text = reply_to.text or reply_to.caption or ""
                url_in_text = text[entity.offset : entity.offset + entity.length]
                reply_urls.append(url_in_text)
                
        # B. 从文本正则提取 (兜底，防止实体未解析)
        if not reply_urls:
            reply_text = reply_to.text or reply_to.caption or ""
            from web_summary import extract_urls
            found = extract_urls(reply_text)
            logger.info(f"Regex found URLs: {found}")
            reply_urls = found
        
        # 去重
        reply_urls = list(set(reply_urls))
        logger.info(f"Final detected reply_urls: {reply_urls}")

        from web_summary import fetch_webpage_content
        
        if reply_urls:
            # 发现 URL，尝试获取内容
            # 先发送一个提示，避免用户以为卡死
            status_msg = await update.message.reply_text("📄 正在获取引用网页内容...")
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            
            try:
                web_content = await fetch_webpage_content(reply_urls[0])
                if web_content:
                    extra_context = f"【引用网页内容】\n{web_content}\n\n"
                    # 获取成功，删除提示消息
                    await status_msg.delete()
                else:
                    # 获取失败，提示 AI 告知用户
                    extra_context = (
                        "【系统提示】引用的网页链接无法访问（无法提取内容，可能是反爬虫限制）。"
                        "请在回答中明确告知用户你无法读取该链接的内容，并仅根据现有的文本信息进行回答。"
                        "\n\n"
                    )
                    await status_msg.delete()
            except Exception as e:
                logger.error(f"Error fetching reply URL: {e}")
                # 出错也提示 AI
                extra_context = "【系统提示】读取链接时发生错误。请告知用户无法访问该链接。\n\n"
                await status_msg.delete()

        # 2. 处理媒体
        if reply_to.video:
            has_media = True
            video = reply_to.video
            file_id = video.file_id
            mime_type = video.mime_type or "video/mp4"
            
            # 优先检查本地缓存
            from database import get_video_cache
            cache_path = await get_video_cache(file_id)
            
            if cache_path:
                import os
                if os.path.exists(cache_path):
                    logger.info(f"Using cached video: {cache_path}")
                    thinking_msg = await update.message.reply_text("🎬 正在分析视频（使用缓存）...")
                    with open(cache_path, "rb") as f:
                        media_data = bytearray(f.read())
                else:
                    # 缓存文件不存在
                    pass 
            
            # 缓存未命中，通过 Telegram API 下载
            if media_data is None:
                # 检查大小限制（Telegram API 限制 20MB）
                if video.file_size and video.file_size > 20 * 1024 * 1024:
                    await update.message.reply_text(
                        "⚠️ 引用的视频文件过大（超过 20MB），无法通过 Telegram 下载分析。\n\n"
                        "提示：Bot 下载的视频会被缓存，可以直接分析。"
                    )
                    return
                thinking_msg = await update.message.reply_text("🎬 正在下载并分析视频...")
                file = await context.bot.get_file(video.file_id)
                media_data = await file.download_as_bytearray()
                
        elif reply_to.photo:
            has_media = True
            photo = reply_to.photo[-1]
            mime_type = "image/jpeg"
            thinking_msg = await update.message.reply_text("🔍 正在分析图片...")
            file = await context.bot.get_file(photo.file_id)
            media_data = await file.download_as_bytearray()
    
    if not has_media:
        # 普通文本对话
        thinking_msg = await update.message.reply_text(THINKING_MESSAGE)
    
    # 将网页上下文合并到用户消息中
    if extra_context:
        user_message = extra_context + "用户请求：" + user_message

    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        if has_media and media_data:
            # 带媒体的请求
            contents = [
                {
                    "parts": [
                        {"text": user_message},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(bytes(media_data)).decode("utf-8"),
                            }
                        },
                    ]
                }
            ]
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config={
                    "system_instruction": "你是一个友好的助手，可以分析图片和视频内容并回答问题。请用中文回复。",
                },
            )
            if response.text:
                await thinking_msg.edit_text(response.text)
            else:
                await thinking_msg.edit_text("抱歉，我无法分析这个内容。")
        else:
            # 纯文本对话（流式响应 + 多轮上下文）
            from user_context import get_user_context, add_message
            
            # 添加用户消息到上下文
            await add_message(user_id, "user", user_message)
            
            # 获取对话历史
            context_messages = await get_user_context(user_id)
            
            response = gemini_client.models.generate_content_stream(
                model=GEMINI_MODEL,
                contents=context_messages,
                config={
                    "system_instruction": "你是一个友好的助手，可以帮助用户解答问题。请用中文回复。记住之前的对话内容。",
                },
            )

            # 流式响应
            full_response = ""
            last_update_time = 0

            for chunk in response:
                if chunk.text:
                    full_response += chunk.text

                    # 每 0.5 秒更新一次消息
                    now = time.time()
                    if now - last_update_time > 0.5:
                        try:
                            await thinking_msg.edit_text(full_response)
                        except BadRequest:
                            pass
                        last_update_time = now

            # 最终更新完整回复
            if full_response:
                try:
                    await thinking_msg.edit_text(full_response)
                    # 保存 AI 回复到上下文
                    await add_message(user_id, "model", full_response)
                    # 记录统计
                    from stats import increment_stat
                    await increment_stat(user_id, "ai_chats")
                except BadRequest:
                    pass
            else:
                await thinking_msg.edit_text("抱歉，我无法生成回复。请稍后再试。")

    except Exception as e:
        logger.error(f"AI chat error: {e}")
        try:
            await thinking_msg.edit_text(
                "❌ AI 服务出现错误，请稍后再试。\n\n"
                "如需下载视频，请点击 /download 进入下载模式。"
            )
        except BadRequest:
            pass


async def handle_ai_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理图片消息，使用 Gemini AI 分析图片
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    from config import is_user_allowed
    if not is_user_allowed(user_id):
        await update.message.reply_text(
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取图片（选择最大分辨率）
    photo = update.message.photo[-1]
    caption = update.message.caption or "请描述这张图片"
    
    # 立即发送"正在分析"提示
    thinking_msg = await update.message.reply_text("🔍 正在分析图片...")
    
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
            await thinking_msg.edit_text(response.text)
            # 记录统计
            from stats import increment_stat
            await increment_stat(user_id, "photo_analyses")
        else:
            await thinking_msg.edit_text("抱歉，我无法分析这张图片。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI photo analysis error: {e}")
        try:
            await thinking_msg.edit_text("❌ 图片分析失败，请稍后再试。")
        except BadRequest:
            pass


async def handle_ai_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理视频消息，使用 Gemini AI 分析视频
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    from config import is_user_allowed
    if not await is_user_allowed(user_id):
        await update.message.reply_text(
            "⛔ 抱歉，您没有使用 AI 功能的权限。"
        )
        return
    
    # 获取视频
    video = update.message.video
    if not video:
        return
    
    caption = update.message.caption or "请分析这个视频的内容"
    
    # 检查视频大小（Gemini 有限制）
    if video.file_size and video.file_size > 20 * 1024 * 1024:  # 20MB 限制
        await update.message.reply_text(
            "⚠️ 视频文件过大（超过 20MB），无法分析。\n\n"
            "请尝试发送较短的视频片段。"
        )
        return
    
    # 立即发送"正在分析"提示
    thinking_msg = await update.message.reply_text("🎬 正在分析视频，这可能需要一些时间...")
    
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
            await thinking_msg.edit_text(response.text)
            # 记录统计
            from stats import increment_stat
            await increment_stat(user_id, "video_analyses")
        else:
            await thinking_msg.edit_text("抱歉，我无法分析这个视频。请稍后再试。")
        
    except Exception as e:
        logger.error(f"AI video analysis error: {e}")
        try:
            await thinking_msg.edit_text(
                "❌ 视频分析失败，请稍后再试。\n\n"
                "可能的原因：\n"
                "• 视频格式不支持\n"
                "• 视频时长过长\n"
                "• 服务暂时不可用"
            )
        except BadRequest:
            pass
