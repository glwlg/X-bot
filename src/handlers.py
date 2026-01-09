"""
Telegram 消息处理器模块
"""
import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import (
    WAITING_FOR_VIDEO_URL, 
    WAITING_FOR_IMAGE_PROMPT,
    WAITING_FOR_REMIND_INPUT,
    WAITING_FOR_MONITOR_KEYWORD,
    WAITING_FOR_SUBSCRIBE_URL
)
from utils import extract_video_url
from downloader import download_video

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "👋 <b>欢迎使用多平台媒体助手！</b>\n\n"
    "我是一个功能强大的 AI 助手，支持以下功能：\n\n"
    "🎬 <b>多媒体处理</b>\n"
    "• 下载 YouTube, X, TikTok, Bilibili 视频\n"
    "• 支持视频转音频 (MP3) 下载\n"
    "• 视频/图片内容 AI 分析\n\n"
    "🤖 <b>AI 智能助手</b>\n"
    "• 多轮上下文对话\n"
    "• 语音转文字与回复\n"
    "• 网页链接自动摘要\n"
    "• 🌍 沉浸式翻译 (/translate)\n"
    "• ⏰ 定时提醒 (/remind)\n"
    "• 📢 订阅监控 (/monitor)\n"
    "• 文档分析 (PDF/Word)\n"
    "• AI 绘画\n\n"
    "请点击下方按钮开始使用："
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令，显示欢迎消息和功能菜单"""
    keyboard = [
        [
            InlineKeyboardButton("📹 下载视频", callback_data="download_video"),
            InlineKeyboardButton("💬 AI 对话", callback_data="ai_chat"),
        ],
        [
            InlineKeyboardButton("🎨 AI 画图", callback_data="generate_image"),
            InlineKeyboardButton("📢 订阅", callback_data="list_subs"),
        ],
        [
            InlineKeyboardButton("🌍 翻译(开关)", callback_data="toggle_translation"),
            InlineKeyboardButton("⏰ 提醒", callback_data="remind_help"),
        ],
        [
            InlineKeyboardButton("ℹ️ 帮助", callback_data="help"),
            InlineKeyboardButton("⚙️ 设置", callback_data="settings"),
        ],
        [
            InlineKeyboardButton("📊 支持的平台", callback_data="platforms"),
            InlineKeyboardButton("📈 使用统计", callback_data="stats"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(
        WELCOME_MESSAGE,
        reply_markup=reply_markup,
    )


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
        await query.edit_message_text(
            "📹 <b>视频下载模式</b>\n\n"
            "请选择下载格式：",
            parse_mode="HTML",
            reply_markup=reply_markup,
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
        await query.edit_message_text(
            f"📹 <b>视频下载模式</b>\n\n"
            f"已选择：{format_text}\n\n"
            "请发送视频链接，支持以下平台：\n"
            "• X (Twitter)\n"
            "• YouTube\n"
            "• Instagram\n"
            "• TikTok\n"
            "• Bilibili\n\n"
            "发送 /cancel 取消操作。",
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        
    return WAITING_FOR_VIDEO_URL


async def start_generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """进入 AI 画图模式的入口"""
    query = update.callback_query
    await query.answer()
    
    logger.info("Entering image generation mode")
    keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            "🎨 <b>AI 画图模式</b>\n\n"
            "请发送您想要生成的图片描述。\n\n"
            "💡 提示：\n"
            "• 描述越详细，生成效果越好\n"
            "• 可以包含风格、颜色、氛围等元素\n"
            "• AI 会自动优化您的提示词\n\n"
            "示例：一只可爱的橘猫在樱花树下\n\n"
            "发送 /cancel 取消操作。",
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Error editing message in start_generate_image: {e}")
        
    return WAITING_FOR_IMAGE_PROMPT


async def back_to_main_and_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """返回主菜单并取消当前操作（用于在对话状态中）"""
    query = update.callback_query
    await query.answer()
    
    logger.info("Back to main menu and cancel current operation")
    
    keyboard = [
        [
            InlineKeyboardButton("📹 下载视频", callback_data="download_video"),
            InlineKeyboardButton("💬 AI 对话", callback_data="ai_chat"),
        ],
        [
            InlineKeyboardButton("🎨 AI 画图", callback_data="generate_image"),
            InlineKeyboardButton("📢 订阅", callback_data="list_subs"),
        ],
        [
            InlineKeyboardButton("🌍 翻译(开关)", callback_data="toggle_translation"),
            InlineKeyboardButton("⏰ 提醒", callback_data="remind_help"),
        ],
        [
            InlineKeyboardButton("ℹ️ 帮助", callback_data="help"),
            InlineKeyboardButton("⚙️ 设置", callback_data="settings"),
        ],
        [
            InlineKeyboardButton("📊 支持的平台", callback_data="platforms"),
            InlineKeyboardButton("📈 使用统计", callback_data="stats"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            WELCOME_MESSAGE,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Error in back_to_main_and_cancel: {e}")
    
    return ConversationHandler.END


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理通用内联键盘按钮点击（非会话入口）"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    logger.info(f"Button clicked: {data}")

    try:
        if data == "ai_chat":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "💬 <b>AI 对话模式</b>\n\n"
                "现在您可以直接发送任何消息，我会用 AI 智能回复！\n\n"
                "💡 提示：直接在对话框输入消息即可，无需点击按钮。",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "help":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "ℹ️ <b>使用帮助</b>\n\n"
                "<b>AI 智能对话：</b>\n"
                "• <b>多轮对话</b>：直接发送文本，AI 会记住上下文\n"
                "• <b>语音对话</b>：发送语音消息，AI 会听并回复\n"
                "• <b>图片分析</b>：发送图片 + 问题\n"
                "• <b>视频分析</b>：发送/引用视频 + 问题\n"
                "• <b>文档分析</b>：发送 PDF/Word 文档\n"
                "• <b>网页摘要</b>：发送链接，AI 自动生成摘要\n"
                "• <b>沉浸式翻译</b>：输入 /translate 开启中英互译\n"
                "• <b>定时提醒</b>：/remind 10m 喝水\n"
                "• <b>订阅监控</b>：/monitor Apple (监控新闻)\n\n"
                "<b>多媒体下载：</b>\n"
                "1. 点击「📹 下载视频」\n"
                "2. 选择 <b>视频</b> 或 <b>仅音频(MP3)</b>\n"
                "3. 发送链接 (YouTube, TikTok, Bilibili等)\n"
                "4. 💡 <b>秒传功能</b>：已下载过的视频会立即发送\n\n"
                "<b>AI 画图：</b>\n"
                "• 点击「🎨 AI 画图」或用 /image\n"
                "• 发送描述，AI 自动优化提示词并绘图\n\n"
                "<b>其他命令：</b>\n"
                "/stats - 查看使用统计\n"
                "/start - 主菜单\n"
                "/cancel - 取消当前操作",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "settings":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # 安全获取环境变量
            openai_model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
            gemini_model = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
            image_model = os.getenv('IMAGE_MODEL', 'imagen-3.0-generate-002')
            
            await query.edit_message_text(
                "⚙️ <b>设置</b>\n\n"
                "当前配置：\n"
                f"• Gemini 模型：{gemini_model}\n"
                f"• 画图模型：{image_model}\n"
                f"• 视频质量：最高\n"
                f"• 文件大小限制：49 MB\n\n"
                "更多设置功能即将推出...",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "platforms":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📊 <b>支持的视频平台</b>\n\n"
                "✅ X (Twitter) - twitter.com, x.com\n"
                "✅ YouTube - youtube.com, youtu.be\n"
                "✅ Instagram - instagram.com\n"
                "✅ TikTok - tiktok.com\n"
                "✅ Bilibili - bilibili.com\n\n"
                "支持绝大多数公开视频链接！",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "stats":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            from stats import get_user_stats_text
            user_id = query.from_user.id
            stats_text = await get_user_stats_text(user_id)
            
            await query.edit_message_text(
                stats_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "list_subs":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            user_id = query.from_user.id
            from database import get_user_subscriptions
            subs = await get_user_subscriptions(user_id)
            
            if not subs:
                text = (
                    "📢 <b>我的订阅</b>\n\n"
                    "您还没有订阅任何内容。\n\n"
                    "<b>使用方法：</b>\n"
                    "• /subscribe &lt;URL&gt; : 订阅 RSS\n"
                    "• /monitor &lt;关键词&gt; : 监控新闻\n"
                )
            else:
                text = "📢 <b>我的订阅列表</b>\n\n"
                for sub in subs:
                    title = sub['title'] or '无标题'
                    url = sub['feed_url']
                    text += f"• [{title}]({url})\n"
                
                text += "\n使用 /unsubscribe &lt;URL&gt; 取消订阅。"
            
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            return ConversationHandler.END
            
        elif data == "toggle_translation":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            user_id = query.from_user.id
            from database import get_user_settings, set_translation_mode
            
            settings = await get_user_settings(user_id)
            current_status = settings.get("auto_translate", 0)
            new_status = not current_status
            await set_translation_mode(user_id, new_status)
            
            status_text = "🌍 <b>已开启</b>" if new_status else "🚫 <b>已关闭</b>"
            desc = (
                "现在发送任何文本消息，我都会为您自动翻译。\n(外语->中文，中文->英文)" 
                if new_status else 
                "已恢复正常 AI 助手模式。"
            )
            
            await query.edit_message_text(
                f"ℹ️ <b>沉浸式翻译模式</b>\n\n"
                f"当前状态：{status_text}\n\n"
                f"{desc}\n\n"
                f"点击按钮可再次切换。",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return ConversationHandler.END
            
        elif data == "remind_help":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "⏰ <b>定时提醒使用帮助</b>\n\n"
                "请直接发送命令设置提醒：\n\n"
                "• <b>/remind 10m 关火</b> (10分钟后)\n"
                "• <b>/remind 1h30m 休息一下</b> (1小时30分后)\n\n"
                "时间单位支持：s(秒), m(分), h(时), d(天)",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return ConversationHandler.END
            
        elif data == "back_to_main":
            # 重新显示主菜单
            keyboard = [
                [
                    InlineKeyboardButton("📹 下载视频", callback_data="download_video"),
                    InlineKeyboardButton("💬 AI 对话", callback_data="ai_chat"),
                ],
                [
                    InlineKeyboardButton("🎨 AI 画图", callback_data="generate_image"),
                    InlineKeyboardButton("📢 订阅", callback_data="list_subs"),
                ],
                [
                    InlineKeyboardButton("🌍 翻译(开关)", callback_data="toggle_translation"),
                    InlineKeyboardButton("⏰ 提醒", callback_data="remind_help"),
                ],
                [
                    InlineKeyboardButton("ℹ️ 帮助", callback_data="help"),
                    InlineKeyboardButton("⚙️ 设置", callback_data="settings"),
                ],
                [
                    InlineKeyboardButton("📊 支持的平台", callback_data="platforms"),
                    InlineKeyboardButton("📈 使用统计", callback_data="stats"),
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                WELCOME_MESSAGE,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Error in button_callback for data {data}: {e}")
        # 尝试通知用户发生错误，如果 edit 失败
        try:
             await query.message.reply_text("❌ 操作失败，请重试或输入 /start 重启。")
        except:
             pass

    return ConversationHandler.END


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /download 命令，进入视频下载模式"""
    await update.message.reply_html(
        "📹 <b>视频下载模式</b>\n\n"
        "请发送视频链接，支持以下平台：\n"
        "• X (Twitter)\n"
        "• YouTube\n"
        "• Instagram\n"
        "• TikTok\n"
        "• Bilibili\n\n"
        "发送 /cancel 取消操作。"
    )
    return WAITING_FOR_VIDEO_URL


async def handle_video_download(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """处理视频下载流程中的 URL 输入"""
    message_text = update.message.text
    if not message_text:
        await update.message.reply_text("请发送有效的视频链接。")
        return WAITING_FOR_VIDEO_URL

    url = extract_video_url(message_text)
    if not url:
        await update.message.reply_text(
            "链接格式似乎不被支持，请检查。\n\n发送 /cancel 取消操作。"
        )
        return WAITING_FOR_VIDEO_URL

    chat_id = update.message.chat_id
    
    # 获取用户选择的下载格式（默认视频）
    audio_only = context.user_data.get("download_format") == "audio"
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
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=processing_message.message_id,
                    text=f"❌ 下载失败: {result.error_message}"
                )
            except:
                pass
        return ConversationHandler.END

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
        
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=processing_message.message_id,
            text=f"⚠️ <b>视频文件过大 ({result.file_size_mb:.1f}MB)</b>\n\n"
                 f"超过 Telegram 限制 (50MB)，无法直接发送。\n"
                 f"您可以选择：",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

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
                await increment_stat(update.message.from_user.id, "downloads")
                
            # 删除进度消息
            await context.bot.delete_message(
                chat_id=chat_id, message_id=processing_message.message_id
            )
            
        except Exception as e:
            logger.error(f"Failed to send video to chat {chat_id}: {e}")
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=processing_message.message_id,
                text="❌ 发送视频失败，可能是网络问题或格式不受支持。",
            )

    return ConversationHandler.END


async def handle_large_file_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理大文件操作的回调 (摘要/音频/删除)"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    file_path = context.user_data.get("large_file_path")
    
    if not file_path or not os.path.exists(file_path):
        await query.edit_message_text("❌ 文件已过期或不存在，请重新下载。")
        return

    chat_id = update.effective_chat.id
    
    try:
        if data == "large_file_delete":
            os.remove(file_path)
            await query.edit_message_text("🗑️ 文件已删除。")
            
        elif data == "large_file_audio":
            await query.edit_message_text("🎵 正在提取音频并发送，请稍候...")
            # 这里调用提取音频逻辑，简单起见先检查如果是 mp3直接发，如果是 mp4 用 ffmpeg 转
            # 由于 download_video 已经支持 mp3，如果是 mp4，我们可能需要转码
            # 但用户也可能一开始就选了 video 格式下载了 mp4
            
            # 简单实现：如果是 mp4，尝试发原文件当音频？不行，Telegram 会认出是视频。
            # 需要转码。
            # 为了保持 handler 简单，我们假设 file_path 如果是 mp4，我们用 ffmpeg 提取
            base, ext = os.path.splitext(file_path)
            if ext.lower() == '.mp4':
                audio_path = f"{base}.mp3"
                if not os.path.exists(audio_path):
                    # 调用 ffmpeg 提取
                    import subprocess
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
                 await query.edit_message_text(f"❌ 提取的音频也超过 50MB，无法发送。")
            else:
                 await context.bot.send_audio(
                    chat_id=chat_id, 
                    audio=open(final_path, "rb"),
                    caption="🎵 仅音频 (从大视频提取)"
                 )
                 await query.delete_message()
                 
        elif data == "large_file_summary":
            await query.edit_message_text("📝 正在提取并压缩音频，请稍候... (这可能需要几分钟)")
            
            # 使用 ffmpeg 提取并压缩音频，确保大小适合 inline传输 (<20MB)
            # 目标：单声道(ac 1), 16kHz(ar 16000), 32kbps(b:a 32k) -> ~14MB/hour
            base, _ = os.path.splitext(file_path)
            compressed_audio_path = f"{base}_compressed.mp3"
            
            import subprocess
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
                await query.edit_message_text("❌ 音频提取失败。")
                return

            # 读取文件并进行 base64 编码 (仿照 voice_handler)
            import base64
            with open(compressed_audio_path, "rb") as f:
                audio_bytes = f.read()
            
            # 检查压缩后大小
            if len(audio_bytes) > 25 * 1024 * 1024:
                await query.edit_message_text("❌ 即使压缩后音频仍然过大，无法分析。")
                os.remove(compressed_audio_path)
                return

            await query.edit_message_text("📝 音频处理完成，正在通过 AI 生成摘要...")

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
                await query.message.reply_text(f"📝 **视频内容摘要**\n\n{response.text}", parse_mode="Markdown")
                await query.delete_message()
            else:
                await query.edit_message_text("❌ AI 无法生成摘要。")

    except Exception as e:
        logger.error(f"Error handling large file action: {e}")
        await query.message.reply_text(f"❌ 操作失败: {str(e)}")


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /image 命令，进入画图模式"""
    await update.message.reply_html(
        "🎨 <b>AI 画图模式</b>\n\n"
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
        await update.message.reply_text("请发送有效的图片描述。")
        return WAITING_FOR_IMAGE_PROMPT
    
    # 调用画图处理函数
    from image_generator import handle_image_generation
    await handle_image_generation(update, context, user_prompt)
    
    return ConversationHandler.END


    return ConversationHandler.END


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /remind 命令，支持交互式输入"""
    args = context.args
    # 如果有参数，直接执行逻辑
    if args and len(args) >= 2:
        await _process_remind(update, context, args[0], " ".join(args[1:]))
        return ConversationHandler.END
        
    # 没有参数，提示输入
    await update.message.reply_text(
        "⏰ <b>设置定时提醒</b>\n\n"
        "请发送您想要的提醒时间和内容。\n"
        "格式：&lt;时间&gt; &lt;内容&gt;\n\n"
        "示例：\n"
        "• 10m 喝水\n"
        "• 1h30m 开会\n"
        "• 20s 测试一下\n\n"
        "发送 /cancel 取消。",
        parse_mode="HTML"
    )
    return WAITING_FOR_REMIND_INPUT


async def handle_remind_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理提醒的交互式输入"""
    text = update.message.text
    if not text:
        await update.message.reply_text("请发送有效文本。")
        return WAITING_FOR_REMIND_INPUT
        
    parts = text.strip().split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ 格式不正确。请同时提供时间和内容，用空格分开。\n"
            "例如：10m 喝水"
        )
        return WAITING_FOR_REMIND_INPUT
        
    success = await _process_remind(update, context, parts[0], parts[1])
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_REMIND_INPUT


async def _process_remind(update: Update, context: ContextTypes.DEFAULT_TYPE, time_str: str, message: str) -> bool:
    """实际处理提醒逻辑（复用）"""
    
    # 解析时间
    import re
    import datetime
    
    # 简单的正则解析：支持单个单位 (e.g. 10m) 或组合 (e.g. 1h30m)
    # 暂时只实现简单的单个单位解析，或者分段解析
    # pattern: findall (\d+)([smhd])
    matches = re.findall(r"(\d+)([smhd])", time_str.lower())
    
    args = context.args
    if not matches:
        await update.message.reply_text("❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。")
        return False
        
    delta_seconds = 0
    for value, unit in matches:
        value = int(value)
        if unit == 's':
            delta_seconds += value
        elif unit == 'm':
            delta_seconds += value * 60
        elif unit == 'h':
            delta_seconds += value * 3600
        elif unit == 'd':
            delta_seconds += value * 86400
            
    if delta_seconds <= 0:
        await update.message.reply_text("❌ 时间必须大于 0。")
        return False
        
    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=delta_seconds)
    
    # 调度任务
    from scheduler import schedule_reminder
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await schedule_reminder(context.job_queue, user_id, chat_id, message, trigger_time)
    
    # 格式化显示的触发时间 (HH:MM:SS)
    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
        
    await update.message.reply_text(
        f"👌 已设置提醒：{message}\n"
        f"⏰ 将在 {display_time} 提醒你。"
    )
    return True


async def toggle_translation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /translate 命令，切换沉浸式翻译模式"""
    user_id = update.effective_user.id
    
    from database import get_user_settings, set_translation_mode
    
    # 获取当前状态
    settings = await get_user_settings(user_id)
    current_status = settings.get("auto_translate", 0)
    
    # 切换状态
    new_status = not current_status
    await set_translation_mode(user_id, new_status)
    
    if new_status:
        await update.message.reply_text(
            "🌍 **沉浸式翻译模式：已开启**\n\n"
            "现在发送任何文本消息，我都会为您自动翻译。\n"
            "• 外语 -> 中文\n"
            "• 中文 -> 英文\n\n"
            "再次输入 /translate 可关闭。"
        )
    else:
        await update.message.reply_text(
            "🚫 **沉浸式翻译模式：已关闭**\n\n"
            "已恢复正常 AI 助手模式。"
        )


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /subscribe 命令，支持交互式输入"""
    args = context.args
    if args:
        await _process_subscribe(update, context, args[0])
        return ConversationHandler.END
        
    # 无参数，提示输入
    await update.message.reply_text(
        "📢 <b>订阅 RSS 源</b>\n\n"
        "请发送您想订阅的 RSS 链接。\n"
        "Bot 将每 30 分钟检查更新。\n\n"
        "示例：\n"
        "https://feeds.feedburner.com/PythonInsider\n\n"
        "发送 /cancel 取消。",
        parse_mode="HTML"
    )
    return WAITING_FOR_SUBSCRIBE_URL


async def handle_subscribe_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 RSS 链接的输入"""
    url = update.message.text
    if not url:
        await update.message.reply_text("请发送有效的链接。")
        return WAITING_FOR_SUBSCRIBE_URL
        
    success = await _process_subscribe(update, context, url)
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_SUBSCRIBE_URL


async def _process_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> bool:
    """实际处理订阅逻辑"""
    user_id = update.effective_user.id
    
    # 简单的 URL 校验
    if not url.startswith("http"):
        await update.message.reply_text("❌ 请输入有效的 HTTP/HTTPS 链接。")
        return False

    # 限制每人最多 5 个
    from database import get_user_subscriptions, add_subscription
    current_subs = await get_user_subscriptions(user_id)
    if len(current_subs) >= 5:
        await update.message.reply_text("❌ 订阅数量已达上限 (5个)。请先取消一些订阅。")
        return False
        
    # 尝试解析 RSS 验证有效性
    import feedparser
    # 简单的验证，不阻塞太久
    try:
        msg = await update.message.reply_text("🔍 正在验证 RSS 源...")
        # 异步运行 feedparser
        feed = feedparser.parse(url)
        
        # 暂时忽略 bozo，只要有 entries 或 title 就行
             
        title = feed.feed.get("title", url)
        if not title:
             title = url
             
        # 入库
        try:
            await add_subscription(user_id, url, title)
            await msg.edit_text(f"✅ **订阅成功！**\n\n源：{title}\nBot 将每 30 分钟检查一次更新。")
            return True
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                await msg.edit_text("⚠️ 您已经订阅过这个源了。")
                return True # 算作成功
            else:
                 await msg.edit_text(f"❌ 订阅失败: {e}")
                 return False
                 
    except Exception as e:
        logger.error(f"Subscribe error: {e}")
        await msg.edit_text("❌ 无法访问该 RSS 源。")
        return False


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /unsubscribe 命令"""
    # 如果有参数，直接取消该 URL
    # 如果没参数，显示列表按钮（简化起见，让用户复制 URL）
    args = context.args
    if not args:
         await update.message.reply_text("⚠️ 用法：/unsubscribe <RSS链接>\n请使用 /list_subs 查看您的订阅链接。")
         return
         
    url = args[0]
    user_id = update.effective_user.id
    
    from database import delete_subscription
    await delete_subscription(user_id, url)
    
    await update.message.reply_text(f"🗑️ 已取消订阅：{url}")


async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /monitor 命令，支持交互式输入"""
    args = context.args
    # 如果有参数，直接执行
    if args:
        await _process_monitor(update, context, " ".join(args))
        return ConversationHandler.END
        
    # 无参数，提示输入
    await update.message.reply_text(
        "🔍 <b>监控关键词</b>\n\n"
        "请发送您想监控的关键词。\n"
        "Bot 将通过 Google News 监控并在有新内容时通知您。\n\n"
        "示例：\n"
        "• Python 教程\n"
        "• 人工智能\n\n"
        "发送 /cancel 取消。",
        parse_mode="HTML"
    )
    return WAITING_FOR_MONITOR_KEYWORD


async def handle_monitor_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理监控关键词的输入"""
    keyword = update.message.text
    if not keyword:
        await update.message.reply_text("请发送有效文本。")
        return WAITING_FOR_MONITOR_KEYWORD
        
    success = await _process_monitor(update, context, keyword)
    if success:
        return ConversationHandler.END
    else:
        # 如果失败（非重复订阅错误），允许重试
        return WAITING_FOR_MONITOR_KEYWORD


async def _process_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword: str) -> bool:
    """实际处理监控逻辑"""
    user_id = update.effective_user.id
    
    # 限制每人最多 5 个 (与普通订阅共享额度)
    from database import get_user_subscriptions, add_subscription
    current_subs = await get_user_subscriptions(user_id)
    if len(current_subs) >= 5:
        await update.message.reply_text("❌ 订阅数量已达上限 (5个)。请先取消一些订阅。")
        return False

    # 构造 Google News RSS URL
    import urllib.parse
    encoded_keyword = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    
    msg = await update.message.reply_text(f"🔍 正在为关键词 '{keyword}' 配置监控...")
    
    try:
        # 验证一下 RSS (虽然 Google News 通常没问题)
        import feedparser
        feed = feedparser.parse(rss_url)
        
        # Google News RSS title通常是 "Google News - keyword"
        title = f"监控: {keyword}"
        
        await add_subscription(user_id, rss_url, title)
        await msg.edit_text(
            f"✅ **监控已设置！**\n\n"
            f"关键词：{keyword}\n"
            f"来源：Google News\n"
            f"Bot 将每 30 分钟推送相关新闻。"
        )
        return True
            
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
             await msg.edit_text("⚠️ 您已经监控过这个关键词了。")
             return True # 算作成功结束，不再 retry
        else:
             logger.error(f"Monitor error: {e}")
             await msg.edit_text(f"❌ 设置失败: {e}")
             return False


async def list_subs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /list_subs 命令"""
    user_id = update.effective_user.id
    
    from database import get_user_subscriptions
    subs = await get_user_subscriptions(user_id)
    
    if not subs:
        await update.message.reply_text("📭 您当前没有订阅任何 RSS 源。")
        return
        
    msg = "📋 **您的订阅列表**：\n\n"
    for sub in subs:
        title = sub["title"]
        url = sub["feed_url"]
        msg += f"• [{title}]({url})\n  `{url}`\n\n"
        
    msg += "发送 `/unsubscribe <链接>` 可取消订阅。"
    
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """取消当前操作"""
    await update.message.reply_text(
        "操作已取消。\n\n" "发送消息继续 AI 对话，或使用 /download 下载视频。"
    )
    return ConversationHandler.END
