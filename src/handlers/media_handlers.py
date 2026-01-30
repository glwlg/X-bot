import os
import asyncio
import logging
from core.platform.models import UnifiedContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

from core.config import WAITING_FOR_VIDEO_URL
from utils import extract_video_url, smart_edit_text, smart_reply_text
from services.download_service import download_video
from .base_handlers import check_permission_unified
from user_context import add_message

logger = logging.getLogger(__name__)

# --- Video Download ---

async def download_command(ctx: UnifiedContext) -> int:
    """处理 /download 命令，进入视频下载模式"""
    if not await check_permission_unified(ctx):
        return ConversationHandler.END

    await ctx.reply(
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

async def start_download_video(ctx: UnifiedContext) -> int:
    """进入视频下载模式的入口"""
    query = ctx.platform_event.callback_query
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
        await ctx.edit_message(
            query.message.message_id,
            "📹 **视频下载模式**\n\n"
            "请选择下载格式：",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error editing message in start_download_video: {e}")
        
    return WAITING_FOR_VIDEO_URL


async def handle_download_format(ctx: UnifiedContext) -> int:
    """处理下载格式选择"""
    query = ctx.platform_event.callback_query
    await query.answer()
    
    data = query.data
    
    if not ctx.platform_ctx:
         return ConversationHandler.END

    # 存储用户选择的格式
    if data == "dl_format_video":
        ctx.platform_ctx.user_data["download_format"] = "video"
        format_text = "📹 视频（最佳质量）"
    else:
        ctx.platform_ctx.user_data["download_format"] = "audio"
        format_text = "🎵 仅音频 (MP3)"
    
    keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await ctx.edit_message(query.message.message_id,
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
    ctx: UnifiedContext
) -> int:
    """处理视频下载流程中的 URL 输入"""
    message_text = ctx.message.text
    if not message_text:
        await ctx.reply("请发送有效的视频链接。")
        return WAITING_FOR_VIDEO_URL

    url = extract_video_url(message_text)
    if not url:
        await ctx.reply(
            "链接格式似乎不被支持，请检查。\n\n发送 /cancel 取消操作。"
        )
        return WAITING_FOR_VIDEO_URL

    if not ctx.platform_ctx:
        return ConversationHandler.END

    chat_id = ctx.message.chat.id
    
    # 获取用户选择的下载格式（默认视频）
    audio_only = ctx.platform_ctx.user_data.get("download_format") == "audio"
    
    # Delegate to the shared processing function
    await process_video_download(ctx, url, audio_only)

    return ConversationHandler.END


async def process_video_download(ctx: UnifiedContext, url: str, audio_only: bool = False) -> None:
    """
    Core video download logic, shared by direct command and AI router.
    """
    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id
    
    if not ctx.platform_ctx:
         logger.error("Platform context missing in process_video_download")
         return

    format_text = "音频" if audio_only else "视频"

    processing_message = await ctx.reply(f"正在下载{format_text}，请稍候... ⏳")

    # 下载视频/音频
    result = await download_video(url, chat_id, processing_message, audio_only=audio_only)

    if not result.success:
        # 失败已在 downloader 中通过 progress_message 提示过，或者返回了 error_message
        if result.error_message:
             # 尝试更新消息显示错误（如果 downloader 没做）
            try:
                await ctx.edit_message(processing_message.message_id, f"❌ 下载失败: {result.error_message}")
            except:
                pass
        return

    file_path = result.file_path
    
    # 处理文件过大情况
    if result.is_too_large:
        # 暂存路径到 user_data以供后续操作
        ctx.platform_ctx.user_data["large_file_path"] = file_path
        
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
        
        await ctx.edit_message(processing_message.message_id,
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
                await ctx.reply_audio(
                    audio=open(file_path, "rb"),
                    caption="🎵 仅音频 (视频提取)"
                )
                # 音频文件也保留以避免重复下载
            else:
                # 发送视频并获取返回的消息（包含 file_id）
                # ctx.reply_video returns the message object (Telegram Message)
                sent_message = await ctx.reply_video(
                    video=open(file_path, "rb"),
                    supports_streaming=True
                )
                
                # 记录视频文件路径以供 AI 分析
                if sent_message.video:
                    from repositories import save_video_cache
                    
                    file_id = sent_message.video.file_id
                    # 直接存储当前路径（已经在 DOWNLOAD_DIR 中）
                    await save_video_cache(file_id, file_path)
                    logger.info(f"Video cached: {file_id} -> {file_path}")
                
                # 记录统计
                from stats import increment_stat
                try:
                    await increment_stat(int(user_id), "downloads")
                except:
                    pass
                
            # 删除进度消息
            await ctx.delete_message(message_id=processing_message.message_id)
            
        except Exception as e:
            logger.error(f"Failed to send video to chat {chat_id}: {e}")
            await ctx.edit_message(processing_message.message_id, "❌ 发送视频失败，可能是网络问题或格式不受支持。")

async def handle_video_actions(ctx: UnifiedContext) -> None:
    """处理视频链接的智能选项（下载 vs 摘要）"""
    query = ctx.platform_event.callback_query
    await query.answer()
    
    if not ctx.platform_ctx:
         return

    url = ctx.platform_ctx.user_data.get('pending_video_url')
    if not url:
        await ctx.edit_message(query.message.message_id, "❌ 链接已过期，请重新发送。")
        return

    action = query.data
    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id
    
    if action == "action_download_video":
        await ctx.edit_message(query.message.message_id, "📹 准备下载视频...")
        
        processing_message = await ctx.reply(f"正在下载视频，请稍候... ⏳")
        
        # 调用下载逻辑
        result = await download_video(url, chat_id, processing_message, audio_only=False)
        
        if not result.success:
             if result.error_message:
                try:
                    await ctx.edit_message(processing_message.message_id, f"❌ 下载失败: {result.error_message}")
                except:
                    pass
             return

        file_path = result.file_path
        
        # 处理文件过大 (复用 handle_video_download 的逻辑) - Refactor opportunity: extract common logic
        if result.is_too_large:
            ctx.platform_ctx.user_data["large_file_path"] = file_path
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
            
            await ctx.edit_message(processing_message.message_id,
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
                sent_message = await ctx.reply_video(
                    video=open(file_path, "rb"),
                    supports_streaming=True
                )
                
                # 缓存
                if sent_message.video:
                    from repositories import save_video_cache
                    file_id = sent_message.video.file_id
                    await save_video_cache(file_id, file_path)
                
                # 统计
                from stats import increment_stat
                try:
                    await increment_stat(int(user_id), "downloads")
                except:
                    pass
                
                # 删除进度消息
                await ctx.delete_message(message_id=processing_message.message_id)
            except Exception as e:
                logger.error(f"Failed to send video: {e}")
                await ctx.edit_message(processing_message.message_id, "❌ 发送视频失败。")

    elif action == "action_summarize_video":
        await ctx.edit_message(query.message.message_id, "📄 正在获取网页内容并生成摘要...")
        try:
           await ctx.send_chat_action(action="typing")
        except:
           pass
        
        from services.web_summary_service import summarize_webpage
        summary = await summarize_webpage(url)
        
        await ctx.edit_message(query.message.message_id, summary)
        
        # Save summary to history
        try:
             await add_message(ctx.platform_ctx, int(user_id), "model", summary)
        except:
             pass
        
        # 统计
        from stats import increment_stat
        try:
             await increment_stat(int(user_id), "video_summaries")
        except:
             pass


async def handle_large_file_action(ctx: UnifiedContext) -> None:
    """处理大文件操作的回调 (摘要/音频/删除)"""
    query = ctx.platform_event.callback_query
    await query.answer()
    
    if not ctx.platform_ctx:
         return

    data = query.data
    file_path = ctx.platform_ctx.user_data.get("large_file_path")
    
    if not file_path or not os.path.exists(file_path):
        await ctx.edit_message(query.message.message_id, "❌ 文件已过期或不存在，请重新下载。")
        return

    chat_id = ctx.message.chat.id
    
    try:
        if data == "large_file_delete":
            try:
                os.remove(file_path)
            except:
                pass
            await ctx.edit_message(query.message.message_id, "🗑️ 文件已删除。")
            
        elif data == "large_file_audio":
            await ctx.edit_message(query.message.message_id, "🎵 正在提取音频并发送，请稍候...")
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
            if os.path.getsize(final_path) > 50 * 1024 * 1024:
                 await ctx.edit_message(query.message.message_id, f"❌ 提取的音频也超过 50MB，无法发送。")
            else:
                 await ctx.platform_ctx.bot.send_audio(
                    chat_id=chat_id, 
                    audio=open(final_path, "rb"),
                    caption="🎵 仅音频 (从大视频提取)"
                 )
                 try:
                    await query.delete_message()
                 except:
                     pass
                 
                 
        elif data == "large_file_summary":
            await ctx.edit_message(query.message.message_id, "📝 正在提取并压缩音频，请稍候... (这可能需要几分钟)")
            
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
                await ctx.edit_message(query.message.message_id, "❌ 音频提取失败。")
                return

            # 读取文件并进行 base64 编码 (仿照 voice_handler)
            import base64
            with open(compressed_audio_path, "rb") as f:
                audio_bytes = f.read()
            
            # 检查压缩后大小
            if len(audio_bytes) > 25 * 1024 * 1024:
                await ctx.edit_message(query.message.message_id, "❌ 即使压缩后音频仍然过大，无法分析。")
                try:
                    os.remove(compressed_audio_path)
                except:
                    pass
                return

            await ctx.edit_message(query.message.message_id, "📝 音频处理完成，正在通过 AI 生成摘要...")

            # 构造 inline data 请求
            from core.config import gemini_client, GEMINI_MODEL
            
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
            try:
                response = await gemini_client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents
                )
            except Exception as e:
                logger.error(f"Gemini API error: {e}")
                await ctx.edit_message(query.message.message_id, f"❌ AI 分析失败: {e}")
                try: os.remove(compressed_audio_path)
                except: pass
                return
            
            # 清理压缩的临时文件
            try:
                os.remove(compressed_audio_path)
            except:
                pass
            
            if response.text:
                summary_text = f"📝 **视频内容摘要**\n\n{response.text}"
                await ctx.reply(summary_text)
                await add_message(ctx.platform_ctx, int(ctx.message.user.id), "model", summary_text)
                try:
                    await query.delete_message()
                except:
                    pass
            else:
                await ctx.edit_message(query.message.message_id, "❌ AI 无法生成摘要。")

    except Exception as e:
        logger.error(f"Error handling large file action: {e}")
        await ctx.reply(f"❌ 操作失败: {str(e)}")


