import logging
import base64
from telegram import Update, Message
from telegram.ext import ContextTypes

from utils import extract_urls, smart_reply_text, get_video_cache
from web_summary import fetch_webpage_content

logger = logging.getLogger(__name__)

async def process_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str, bytes, str]:
    """
    处理回复引用的消息，提取 URL 内容、图片或视频数据。
    
    Returns:
        tuple: (has_media, extra_context, media_data, mime_type)
    """
    reply_to = update.message.reply_to_message
    if not reply_to:
        return False, "", None, None

    has_media = False
    media_data = None
    mime_type = None
    extra_context = ""
    chat_id = update.effective_chat.id

    # 1. 尝试提取引用消息中的 URL 并获取内容
    reply_urls = []
    
    # DEBUG LOG
    logger.info(f"Checking reply_to message {reply_to.message_id} for URLs")
    
    # A. 从实体（超链接/文本链接）提取
    if reply_to.entities:
        for entity in reply_to.entities:
            if entity.type == "text_link":
                reply_urls.append(entity.url)
            elif entity.type == "url":
                reply_urls.append(reply_to.parse_entity(entity))

    if reply_to.caption_entities:
        for entity in reply_to.caption_entities:
            if entity.type == "text_link":
                reply_urls.append(entity.url)
            elif entity.type == "url":
                reply_urls.append(reply_to.parse_caption_entity(entity))
            
    # B. 从文本正则提取 (兜底，防止实体未解析)
    if not reply_urls:
        reply_text = reply_to.text or reply_to.caption or ""
        found = extract_urls(reply_text)
        reply_urls = found
    
    # 去重
    reply_urls = list(set(reply_urls))

    if reply_urls:
        # 发现 URL，尝试获取内容
        # 先发送一个提示，避免用户以为卡死
        status_msg = await smart_reply_text(update, "📄 正在获取引用网页内容...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            web_content = await fetch_webpage_content(reply_urls[0])
            if web_content:
                extra_context = f"【引用网页内容】\n{web_content}\n\n"
                # 获取成功，删除提示消息
                await status_msg.delete()
            else:
                extra_context = (
                    "【系统提示】引用的网页链接无法访问（无法提取内容，可能是反爬虫限制）。"
                    "请在回答中明确告知用户你无法读取该链接的内容，并仅根据现有的文本信息进行回答。"
                    "\n\n"
                )
                await status_msg.delete()
        except Exception as e:
            logger.error(f"Error fetching reply URL: {e}")
            extra_context = "【系统提示】读取链接时发生错误。请告知用户无法访问该链接。\n\n"
            await status_msg.delete()

    # 2. 处理媒体
    if reply_to.video:
        has_media = True
        video = reply_to.video
        file_id = video.file_id
        mime_type = video.mime_type or "video/mp4"
        
        # 优先检查本地缓存
        cache_path = await get_video_cache(file_id)
        
        if cache_path:
            import os
            if os.path.exists(cache_path):
                logger.info(f"Using cached video: {cache_path}")
                await smart_reply_text(update, "🎬 正在分析视频（使用缓存）...")
                with open(cache_path, "rb") as f:
                    media_data = bytearray(f.read())
            else:
                pass 
        
        # 缓存未命中，通过 Telegram API 下载
        if media_data is None:
            # 检查大小限制（Telegram API 限制 20MB）
            if video.file_size and video.file_size > 20 * 1024 * 1024:
                await smart_reply_text(update,
                    "⚠️ 引用的视频文件过大（超过 20MB），无法通过 Telegram 下载分析。\n\n"
                    "提示：Bot 下载的视频会被缓存，可以直接分析。"
                )
                return False, extra_context, None, None # Abort
            
            await smart_reply_text(update, "🎬 正在下载并分析视频...")
            file = await context.bot.get_file(video.file_id)
            media_data = await file.download_as_bytearray()
            
    elif reply_to.photo:
        has_media = True
        photo = reply_to.photo[-1]
        mime_type = "image/jpeg"
        await smart_reply_text(update, "🔍 正在分析图片...")
        file = await context.bot.get_file(photo.file_id)
        media_data = await file.download_as_bytearray()

    elif reply_to.audio or reply_to.voice:
        has_media = True
        if reply_to.audio:
            file_id = reply_to.audio.file_id
            mime_type = reply_to.audio.mime_type or "audio/mpeg"
            file_size = reply_to.audio.file_size
            label = "音频"
        else:
            file_id = reply_to.voice.file_id
            mime_type = reply_to.voice.mime_type or "audio/ogg"
            file_size = reply_to.voice.file_size
            label = "语音"

        # Check size limit (20MB)
        if file_size and file_size > 20 * 1024 * 1024:
            await smart_reply_text(update,
                f"⚠️ 引用的{label}文件过大（超过 20MB），无法通过 Telegram 下载分析。"
            )
             # Abort
            return False, extra_context, None, None

        await smart_reply_text(update, f"🎧 正在分析{label}...")
        file = await context.bot.get_file(file_id)
        media_data = await file.download_as_bytearray()
        
    return has_media, extra_context, media_data, mime_type
