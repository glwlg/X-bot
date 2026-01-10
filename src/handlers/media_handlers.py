import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import WAITING_FOR_VIDEO_URL, WAITING_FOR_IMAGE_PROMPT
from utils import extract_video_url, smart_edit_text, smart_reply_text
from downloader import download_video
from .base_handlers import check_permission

logger = logging.getLogger(__name__)

# --- Video Download ---

async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /download 命令，进入视频下载模式"""
    if not await check_permission(update):
        return ConversationHandler.END

    await smart_reply_text(update,
        "📹 **视频下载模式**\n\n"
        "请发送视频链接，支持以下平台：\n"
        "• X (Twitter)\n"
        "• YouTube\n"
        "• Instagram\n"
        "• TikTok\n"
        "• Bilibili\n\n"
        "发送 /cancel 取消操作。"
    )
    return WAITING_FOR_VIDEO_URL

async def start_download_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """进入视频下载模式的入口"""
    query = update.callback_query
    await query.answer()
    
    logger.info("Entering download video mode")
    
    # 提供下载格式选择
    keyboard = [
        [
            InlineKeyboardButton("📹 视频（最佳质量）", callback_data="dl_format_video"),
            InlineKeyboardButton("🎵 仅音频 (MP3)", callback_data="dl_format_audio"),
        ],
        [
            InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main_cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await smart_edit_text(query.message,
            "📹 **视频下载模式**\n\n"
            "请选择下载格式：",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error editing message in start_download_video: {e}")
        
    return WAITING_FOR_VIDEO_URL


async def handle_download_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理下载格式选择"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # 存储用户选择的格式
    if data == "dl_format_video":
        context.user_data["download_format"] = "video"
        format_text = "📹 视频（最佳质量）"
    else:
        context.user_data["download_format"] = "audio"
        format_text = "🎵 仅音频 (MP3)"
    
    keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await smart_edit_text(query.message,
            f"📹 **视频下载模式**\n\n"
            f"已选择：{format_text}\n\n"
            "请发送视频链接，支持以下平台：\n"
            "• X (Twitter)\n"
            "• YouTube\n"
            "• Instagram\n"
            "• TikTok\n"
            "• Bilibili\n\n"
            "发送 /cancel 取消操作。",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        
    return WAITING_FOR_VIDEO_URL


async def handle_video_download(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """处理视频下载流程中的 URL 输入"""
    message_text = update.message.text
    if not message_text:
        await smart_reply_text(update, "请发送有效的视频链接。")
        return WAITING_FOR_VIDEO_URL

    url = extract_video_url(message_text)
    if not url:
        await smart_reply_text(update,
            "链接格式似乎不被支持，请检查。\n\n发送 /cancel 取消操作。"
        )
        return WAITING_FOR_VIDEO_URL
        return WAITING_FOR_VIDEO_URL

    chat_id = update.message.chat_id
    
    # 获取用户选择的下载格式（默认视频）
    audio_only = context.user_data.get("download_format") == "audio"
    
    # Delegate to the shared processing function
    await process_video_download(update, context, url, audio_only)

    return ConversationHandler.END


async def process_video_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, audio_only: bool = False) -> None:
    """
    Core video download logic, shared by direct command and AI router.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    format_text = "音频" if audio_only else "视频"

    processing_message = await context.bot.send_message(
        chat_id=chat_id, text=f"正在下载{format_text}，请稍候... ⏳"
    )

    # 下载视频/音频
    result = await download_video(url, chat_id, processing_message, audio_only=audio_only)

    if not result.success:
        # 失败已在 downloader 中通过 progress_message 提示过，或者返回了 error_message
        if result.error_message:
             # 尝试更新消息显示错误（如果 downloader 没做）
            try:
                await smart_edit_text(processing_message, f"❌ 下载失败: {result.error_message}")
            except:
                pass
        return

    file_path = result.file_path
    
    # 处理文件过大情况
    if result.is_too_large:
        # 暂存路径到 user_data以供后续操作
        context.user_data["large_file_path"] = file_path
        
        keyboard = [
            [
                InlineKeyboardButton("📝 生成内容摘要 (AI)", callback_data="large_file_summary"),
                InlineKeyboardButton("🎵 仅发送音频", callback_data="large_file_audio"),
            ],
            [
                InlineKeyboardButton("🗑️ 删除文件", callback_data="large_file_delete"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await smart_edit_text(processing_message,
            f"⚠️ **视频文件过大 ({result.file_size_mb:.1f}MB)**\n\n"
            f"超过 Telegram 限制 (50MB)，无法直接发送。\n"
            f"您可以选择：",
            reply_markup=reply_markup
        )
        return

    # 如果下载成功且大小合适，发送文件
    if file_path and os.path.exists(file_path):
        logger.info(f"Downloaded to {file_path}. Uploading to chat {chat_id}.")
        try:
            if audio_only:
                # 发送音频文件
                await context.bot.send_audio(
                    chat_id=chat_id, audio=open(file_path, "rb")
                )
                # 音频文件也保留以避免重复下载
            else:
                # 发送视频并获取返回的消息（包含 file_id）
                sent_message = await context.bot.send_video(
                    chat_id=chat_id, video=open(file_path, "rb"), supports_streaming=True
                )
                
                # 记录视频文件路径以供 AI 分析
                if sent_message.video:
                    from database import save_video_cache
                    
                    file_id = sent_message.video.file_id
                    # 直接存储当前路径（已经在 DOWNLOAD_DIR 中）
                    await save_video_cache(file_id, file_path)
                    logger.info(f"Video cached: {file_id} -> {file_path}")
                
                # 记录统计
                from stats import increment_stat
                await increment_stat(user_id, "downloads")
                
            # 删除进度消息
            await context.bot.delete_message(
                chat_id=chat_id, message_id=processing_message.message_id
            )
            
        except Exception as e:
            logger.error(f"Failed to send video to chat {chat_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to send video to chat {chat_id}: {e}")
            await smart_edit_text(processing_message, "❌ 发送视频失败，可能是网络问题或格式不受支持。")

async def handle_video_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理视频链接的智能选项（下载 vs 摘要）"""
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get('pending_video_url')
    url = context.user_data.get('pending_video_url')
    if not url:
        await smart_edit_text(query.message, "❌ 链接已过期，请重新发送。")
        return

    action = query.data
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if action == "action_download_video":
        await smart_edit_text(query.message, "📹 准备下载视频...")
        
        # 模拟进入下载流程
        processing_message = await context.bot.send_message(
            chat_id=chat_id, text=f"正在下载视频，请稍候... ⏳"
        )
        
        # 调用下载逻辑
        result = await download_video(url, chat_id, processing_message, audio_only=False)
        
        if not result.success:
             if result.error_message:
                try:
                    await smart_edit_text(processing_message, f"❌ 下载失败: {result.error_message}")
                except:
                    pass
             return

        file_path = result.file_path
        
        # 处理文件过大 (复用 handle_video_download 的逻辑)
        if result.is_too_large:
            context.user_data["large_file_path"] = file_path
            keyboard = [
                [
                    InlineKeyboardButton("📝 生成内容摘要 (AI)", callback_data="large_file_summary"),
                    InlineKeyboardButton("🎵 仅发送音频", callback_data="large_file_audio"),
                ],
                [
                    InlineKeyboardButton("🗑️ 删除文件", callback_data="large_file_delete"),
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await smart_edit_text(processing_message,
                f"⚠️ **视频文件过大 ({result.file_size_mb:.1f}MB)**\n\n"
                f"超过 Telegram 限制 (50MB)，无法直接发送。\n"
                f"您可以选择：",
                reply_markup=reply_markup
            )
            return

        # 发送文件
        if file_path and os.path.exists(file_path):
            logger.info(f"Downloaded to {file_path}. Uploading to chat {chat_id}.")
            try:
                sent_message = await context.bot.send_video(
                    chat_id=chat_id, video=open(file_path, "rb"), supports_streaming=True
                )
                
                # 缓存
                if sent_message.video:
                    from database import save_video_cache
                    file_id = sent_message.video.file_id
                    await save_video_cache(file_id, file_path)
                
                # 统计
                from stats import increment_stat
                await increment_stat(user_id, "downloads")
                
                # 删除进度消息
                await context.bot.delete_message(
                    chat_id=chat_id, message_id=processing_message.message_id
                )
            except Exception as e:
                logger.error(f"Failed to send video: {e}")
            except Exception as e:
                logger.error(f"Failed to send video: {e}")
                await smart_edit_text(processing_message, "❌ 发送视频失败。")

    elif action == "action_summarize_video":
        await smart_edit_text(query.message, "📄 正在获取网页内容并生成摘要...")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        from web_summary import summarize_webpage
        summary = await summarize_webpage(url)
        
        await smart_edit_text(query.message, summary)
        
        # 统计
        from stats import increment_stat
        await increment_stat(user_id, "video_summaries")


async def handle_large_file_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理大文件操作的回调 (摘要/音频/删除)"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    file_path = context.user_data.get("large_file_path")
    
    if not file_path or not os.path.exists(file_path):
        await smart_edit_text(query.message, "❌ 文件已过期或不存在，请重新下载。")
        return
        return

    chat_id = update.effective_chat.id
    
    try:
        if data == "large_file_delete":
            os.remove(file_path)
        if data == "large_file_delete":
            os.remove(file_path)
            await smart_edit_text(query.message, "🗑️ 文件已删除。")
            
        elif data == "large_file_audio":
            await smart_edit_text(query.message, "🎵 正在提取音频并发送，请稍候...")
            # 简单实现：如果是 mp4，尝试发原文件当音频？不行，Telegram 会认出是视频。
            # 需要转码。
            base, ext = os.path.splitext(file_path)
            if ext.lower() == '.mp4':
                audio_path = f"{base}.mp3"
                if not os.path.exists(audio_path):
                    # 调用 ffmpeg 提取
                    cmd = [
                        "ffmpeg", "-i", file_path, 
                        "-vn", "-acodec", "libmp3lame", "-q:a", "4", 
                        "-y", audio_path
                    ]
                    process = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                    )
                    await process.wait()
                
                final_path = audio_path
            else:
                final_path = file_path # 假设已经是音频
                
            # 检查音频大小
            # 检查音频大小
            if os.path.getsize(final_path) > 50 * 1024 * 1024:
                 await smart_edit_text(query.message, f"❌ 提取的音频也超过 50MB，无法发送。")
            else:
                 await context.bot.send_audio(
                    chat_id=chat_id, 
                    audio=open(final_path, "rb"),
                    caption="🎵 仅音频 (从大视频提取)"
                 )
                 await query.delete_message()
                 
                 
        elif data == "large_file_summary":
            await smart_edit_text(query.message, "📝 正在提取并压缩音频，请稍候... (这可能需要几分钟)")
            
            # 使用 ffmpeg 提取并压缩音频，确保大小适合 inline传输 (<20MB)
            # 目标：单声道(ac 1), 16kHz(ar 16000), 32kbps(b:a 32k) -> ~14MB/hour
            base, _ = os.path.splitext(file_path)
            compressed_audio_path = f"{base}_compressed.mp3"
            
            cmd = [
                "ffmpeg", 
                "-i", file_path, 
                "-vn",               # 去除视频
                "-acodec", "libmp3lame", 
                "-ac", "1",          # 单声道
                "-ar", "16000",      # 16kHz
                "-b:a", "32k",       # 32kbps
                "-y",                # 覆盖
                compressed_audio_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await process.wait()
            
            if not os.path.exists(compressed_audio_path):
                await smart_edit_text(query.message, "❌ 音频提取失败。")
                return

            # 读取文件并进行 base64 编码 (仿照 voice_handler)
            import base64
            with open(compressed_audio_path, "rb") as f:
                audio_bytes = f.read()
            
            # 检查压缩后大小
            if len(audio_bytes) > 25 * 1024 * 1024:
                await smart_edit_text(query.message, "❌ 即使压缩后音频仍然过大，无法分析。")
                os.remove(compressed_audio_path)
                return

            await smart_edit_text(query.message, "📝 音频处理完成，正在通过 AI 生成摘要...")

            # 构造 inline data 请求
            from config import gemini_client, GEMINI_MODEL
            
            contents = [
                {
                    "parts": [
                        {"text": "请详细总结这段视频音频的内容。请描述主要发生了什么，核心观点是什么，并列出关键时间点 (如果可能)。"},
                        {
                            "inline_data": {
                                "mime_type": "audio/mp3",
                                "data": base64.b64encode(audio_bytes).decode("utf-8"),
                            }
                        },
                    ]
                }
            ]
            
            # Generate content
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents
            )
            
            # 清理压缩的临时文件
            try:
                os.remove(compressed_audio_path)
            except:
                pass
            
            if response.text:
                await smart_reply_text(update, f"📝 **视频内容摘要**\n\n{response.text}")
                await query.delete_message()
            else:
                await smart_edit_text(query.message, "❌ AI 无法生成摘要。")

    except Exception as e:
        logger.error(f"Error handling large file action: {e}")
        await query.message.reply_text(f"❌ 操作失败: {str(e)}")


# --- Image Generation ---

async def start_generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """进入 AI 画图模式的入口"""
    query = update.callback_query
    await query.answer()
    
    logger.info("Entering image generation mode")
    keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await smart_edit_text(query.message,
            "🎨 **AI 画图模式**\n\n"
            "请发送您想要生成的图片描述。\n\n"
            "💡 提示：\n"
            "• 描述越详细，生成效果越好\n"
            "• 可以包含风格、颜色、氛围等元素\n"
            "• AI 会自动优化您的提示词\n\n"
            "示例：一只可爱的橘猫在樱花树下\n\n"
            "发送 /cancel 取消操作。",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error editing message in start_generate_image: {e}")
        
    return WAITING_FOR_IMAGE_PROMPT

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /image 命令，进入画图模式"""
    if not await check_permission(update):
        return ConversationHandler.END

    await smart_reply_text(update,
        "🎨 **AI 画图模式**\n\n"
        "请发送您想要生成的图片描述。\n\n"
        "💡 提示：\n"
        "• 描述越详细，生成效果越好\n"
        "• 可以包含风格、颜色、氛围等元素\n"
        "• AI 会自动优化您的提示词\n\n"
        "示例：一只可爱的橘猫在樱花树下\n\n"
        "发送 /cancel 取消操作。"
    )
    return WAITING_FOR_IMAGE_PROMPT

async def handle_image_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """处理画图提示词输入"""
    user_prompt = update.message.text
    if not user_prompt:
        await smart_reply_text(update, "请发送有效的图片描述。")
        return WAITING_FOR_IMAGE_PROMPT
    
    # 调用画图处理函数
    from image_generator import handle_image_generation
    await handle_image_generation(update, context, user_prompt)
    
    return ConversationHandler.END
