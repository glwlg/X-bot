"""
Telegram 消息处理器模块
"""
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import WAITING_FOR_VIDEO_URL, WAITING_FOR_IMAGE_PROMPT
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
                "• <b>网页摘要</b>：发送链接，AI 自动生成摘要\n\n"
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
                f"• OpenAI 模型：{openai_model}\n"
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
        
        elif data == "back_to_main":
            # 重新显示主菜单
            keyboard = [
                [
                    InlineKeyboardButton("📹 下载视频", callback_data="download_video"),
                    InlineKeyboardButton("💬 AI 对话", callback_data="ai_chat"),
                ],
                [
                    InlineKeyboardButton("🎨 AI 画图", callback_data="generate_image"),
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
    file_path = await download_video(url, chat_id, processing_message, audio_only=audio_only)

    # 如果下载成功，发送文件
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
                
        except Exception as e:
            logger.error(f"Failed to send video to chat {chat_id}: {e}")
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=processing_message.message_id,
                text="❌ 发送视频失败，可能是网络问题或格式不受支持。",
            )
        finally:
            # 无论成功失败，都保留文件在 downloads 目录，供下次秒传
            # 仅删除进度消息
            await context.bot.delete_message(
                chat_id=chat_id, message_id=processing_message.message_id
            )

    return ConversationHandler.END


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


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """取消当前操作"""
    await update.message.reply_text(
        "操作已取消。\n\n" "发送消息继续 AI 对话，或使用 /download 下载视频。"
    )
    return ConversationHandler.END
