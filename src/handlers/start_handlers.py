import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from .base_handlers import check_permission

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

def get_main_menu_keyboard():
    return [
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令，显示欢迎消息和功能菜单"""
    if not await check_permission(update):
        return

    reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())

    await update.message.reply_html(
        WELCOME_MESSAGE,
        reply_markup=reply_markup,
    )

async def back_to_main_and_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """返回主菜单并取消当前操作（用于在对话状态中）"""
    query = update.callback_query
    await query.answer()
    
    logger.info("Back to main menu and cancel current operation")
    
    reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())
    
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
    if not await check_permission(update):
        return ConversationHandler.END

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
                "• Gemini 模型：{gemini_model}\n"
                "• 画图模型：{image_model}\n"
                "• 视频质量：最高\n"
                "• 文件大小限制：49 MB\n\n"
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
                "点击按钮可再次切换。",
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
            reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())
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
