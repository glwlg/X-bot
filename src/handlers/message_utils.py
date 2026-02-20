import logging
import re
import os
import aiofiles

from core.platform.models import UnifiedContext, MessageType
from services.web_summary_service import extract_urls
from core.state_store import get_video_cache
from services.web_summary_service import fetch_webpage_content

logger = logging.getLogger(__name__)


async def process_reply_message(ctx: UnifiedContext) -> tuple[bool, str, bytes, str]:
    """
    处理回复引用的消息，提取 URL 内容、图片或视频数据。

    Returns:
        tuple: (has_media, extra_context, media_data, mime_type)
    """
    reply_to = ctx.message.reply_to_message
    if not reply_to:
        return False, "", None, None

    has_media = False
    media_data = None
    mime_type = None
    extra_context = ""

    # 1. 尝试提取引用消息中的 URL 并获取内容
    reply_urls = []

    # DEBUG LOG
    logger.info(f"Checking reply_to message {reply_to.id} for URLs")

    # A. 从实体（超链接/文本链接）提取
    # A. 从实体（超链接/文本链接）提取
    # UnifiedMessage 可能不包含 entities 属性或 helper 方法，先尝试从 raw_data 或忽略
    # 如果对象也是 Telegram 原生对象 (duck typing)，则保留逻辑，否则跳过
    if hasattr(reply_to, "entities") and reply_to.entities:
        try:
            for entity in reply_to.entities:
                if entity.type == "text_link":
                    reply_urls.append(entity.url)
                elif entity.type == "url" and hasattr(reply_to, "parse_entity"):
                    reply_urls.append(reply_to.parse_entity(entity))
        except Exception as e:
            logger.warning(f"Error parsing entities: {e}")

    if hasattr(reply_to, "caption_entities") and reply_to.caption_entities:
        try:
            for entity in reply_to.caption_entities:
                if entity.type == "text_link":
                    reply_urls.append(entity.url)
                elif entity.type == "url" and hasattr(reply_to, "parse_caption_entity"):
                    reply_urls.append(reply_to.parse_caption_entity(entity))
        except Exception as e:
            logger.warning(f"Error parsing caption entities: {e}")

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
        status_msg = await ctx.reply("📄 正在获取引用网页内容...")
        await ctx.send_chat_action(action="typing")

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
            extra_context = (
                "【系统提示】读取链接时发生错误。请告知用户无法访问该链接。\n\n"
            )
            await status_msg.delete()
    else:
        # 没有 URL，但有纯文本 -> 提取被引用消息的文本作为上下文
        reply_text = reply_to.text or reply_to.caption or ""
        if reply_text:
            # 截断过长的文本
            if len(reply_text) > 2000:
                reply_text = reply_text[:2000] + "...(省略)"
            extra_context = f"【用户引用的消息】\n{reply_text}\n\n"
            logger.info(f"Extracted reply text context: {len(reply_text)} chars")

    # 2. 处理媒体
    if reply_to.type == MessageType.VIDEO:
        has_media = True
        file_id = reply_to.file_id
        mime_type = reply_to.mime_type or "video/mp4"

        # 优先检查本地缓存
        cache_path = await get_video_cache(file_id)

        if cache_path:
            import os

            if os.path.exists(cache_path):
                logger.info(f"Using cached video: {cache_path}")
                await ctx.reply("🎬 正在分析视频（使用缓存）...")
                with open(cache_path, "rb") as f:
                    media_data = bytearray(f.read())
            else:
                pass

        # 缓存未命中，通过 Telegram API 下载
        if media_data is None:
            # 检查大小限制（Telegram API 限制 20MB）
            if reply_to.file_size and reply_to.file_size > 20 * 1024 * 1024:
                await ctx.reply(
                    "⚠️ 引用的视频文件过大（超过 20MB），无法通过 Telegram 下载分析。\n\n"
                    "提示：Bot 下载的视频会被缓存，可以直接分析。"
                )
                return False, extra_context, None, None  # Abort

            await ctx.reply("🎬 正在下载并分析视频...")
            media_data = await ctx.download_file(file_id)

    elif reply_to.type == MessageType.IMAGE:
        has_media = True
        mime_type = "image/jpeg"
        await ctx.reply("🔍 正在分析图片...")
        media_data = await ctx.download_file(reply_to.file_id)

    elif reply_to.type in (MessageType.AUDIO, MessageType.VOICE):
        has_media = True
        file_id = reply_to.file_id
        mime_type = reply_to.mime_type

        if reply_to.type == MessageType.AUDIO:
            if not mime_type:
                mime_type = "audio/mpeg"
            label = "音频"
        else:
            if not mime_type:
                mime_type = "audio/ogg"
            label = "语音"

        file_size = reply_to.file_size

        # Check size limit (20MB)
        if file_size and file_size > 20 * 1024 * 1024:
            await ctx.reply(
                f"⚠️ 引用的{label}文件过大（超过 20MB），无法通过 Telegram 下载分析。"
            )
            # Abort
            return False, extra_context, None, None

        await ctx.reply(f"🎧 正在分析{label}...")
        media_data = await ctx.download_file(file_id)

    return has_media, extra_context, media_data, mime_type


async def process_and_send_code_files(ctx: UnifiedContext, text: str) -> str:
    """
    1. Scan text for code blocks.
    2. If blocks are significant (long), save as file and send to user.
    3. Replace the code block in the original text with a placeholder.
    4. Return the modified text for display.
    """
    if not text:
        return ""

    # Regex to find code blocks: ```language code ```
    code_block_regex = re.compile(r"```(\w+)?\n([\s\S]*?)```")
    matches = list(code_block_regex.finditer(text))

    if not matches:
        return text

    sent_count = 0
    temp_dir = "data/temp_code"
    os.makedirs(temp_dir, exist_ok=True)

    # We will rebuild the text with replacements
    final_text = text
    # Reverse iteration to avoiding index shifting when replacing
    for i, match in enumerate(reversed(matches)):
        # Calculate original index (since we are reversing)
        original_index = len(matches) - 1 - i

        start_pos, end_pos = match.span()
        language = match.group(1).lower().strip() if match.group(1) else "txt"
        code_content = match.group(2).strip()

        if not code_content:
            continue

        # 输出文件策略：除 html 外统一转为 markdown，便于 Telegram 直接预览。
        ext = "html" if language == "html" else "md"

        # Criteria to send as file AND collapse
        lines = code_content.splitlines()
        # If it's JSON -> always send (usually data)
        # If > 10 lines -> send and collapse
        # If > 300 chars -> send and collapse
        should_process = (
            (language == "json") or (len(lines) > 5) or (len(code_content) > 200)
        )

        if not should_process:
            continue

        filename = f"code_snippet_{original_index + 1}.{ext}"
        filepath = os.path.join(temp_dir, filename)

        try:
            if ext == "html":
                file_content = code_content
                caption = "📝 HTML 代码片段"
            else:
                safe_lang = (
                    language if re.fullmatch(r"[a-zA-Z0-9_+\-]+", language) else ""
                )
                if language in {"md", "markdown"}:
                    file_content = code_content
                else:
                    fence = f"```{safe_lang}".rstrip()
                    file_content = f"{fence}\n{code_content}\n```"
                caption = f"📝 Markdown 文本片段（原始语言: {language}）"

            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(file_content)

            # Send document - 读取文件内容为 bytes 以确保跨平台兼容性
            async with aiofiles.open(filepath, "rb") as f:
                file_bytes = await f.read()

            # Platform-adaptive format conversion
            try:
                from services.md_converter import adapt_md_file_for_platform

                platform = getattr(ctx.message, "platform", "") or ""
                file_bytes, filename = adapt_md_file_for_platform(
                    file_bytes=file_bytes,
                    filename=filename,
                    platform=platform,
                )
            except Exception:
                pass

            await ctx.reply_document(
                document=file_bytes,
                filename=filename,
                caption=caption,
                reply_to_message_id=ctx.message.id,
            )
            sent_count += 1

            # Replace in text with placeholder
            placeholder = f"\n\n(⬇️ {language} 内容已保存为文件: {filename})\n\n"
            final_text = final_text[:start_pos] + placeholder + final_text[end_pos:]

        except Exception as e:
            logger.error(f"Failed to send code file {filename}: {e}")

    return final_text
