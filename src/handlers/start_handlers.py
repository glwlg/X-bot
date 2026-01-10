import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from utils import smart_edit_text, smart_reply_text
from .base_handlers import check_permission

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "👋 **欢迎使用 X-Bot！**\n\n"
    "我不仅仅是一个机器人，更是您的智能 AI 伙伴。🧠\n"
    "**现在支持自然语言指令与长期记忆！试着对我发：**\n\n"
    "📥 **下载**\n"
    "• \"帮我下载这个视频 https://...\"\n"
    "• \"保存这段音频 https://...\"\n\n"
    "🧠 **记忆**\n"
    "• \"记住我住在北京市朝阳区\"\n"
    "• \"我上次跟你提到的那个电影叫什么？\"\n\n"
    "🎨 **创作**\n"
    "• \"画一只在太空的猫\"\n\n"
    "⏰ **生活**\n"
    "• \"10分钟后提醒我喝水\"\n"
    "• \"订阅这个RSS源 https://...\"\n"
    "• \"监控关键词 AI News\"\n\n"
    "💬 **对话**\n"
    "• 直接聊天、语音对话、图片分析、网页摘要\n"
    "• \"翻译一下模式\" (/translate)\n\n"
    "当然，您也可以使用下方菜单操作 👇"
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
            InlineKeyboardButton("📊 支持的平台", callback_data="platforms"),
            InlineKeyboardButton("📈 使用统计", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("ℹ️ 帮助", callback_data="help"),
            # InlineKeyboardButton("⚙️ 设置", callback_data="settings"),
        ],
    ]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令，显示欢迎消息和功能菜单"""
    if not await check_permission(update):
        return

    reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())

    await smart_reply_text(update,
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
        await smart_edit_text(query.message,
            WELCOME_MESSAGE,
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
            await smart_edit_text(query.message,
                "💬 **AI 对话模式**\n\n"
                "现在您可以直接发送任何消息，我会用 AI 智能回复！\n\n"
                "💡 提示：直接在对话框输入消息即可，无需点击按钮。",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "help":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await smart_edit_text(query.message,
                "ℹ️ **使用帮助**\n\n"
                "🚀 **新功能：自然语言指令**\n"
                "无需死记硬背命令，直接对我说话即可！\n"
                "• \"下载视频 https://...\"\n"
                "• \"画一张赛博朋克风格的图\"\n"
                "• \"1小时后提醒我开会\"\n"
                "• \"监控关键词 DeepSeek\"\n\n"
                "🧠 **核心能力：长期记忆**\n"
                "我会记住你的偏好和重要信息。\n"
                "• \"记住我的名字叫 Luwei\"\n"
                "• \"我喜欢什么类型的电影？\"\n\n"
                "**🤖 AI 智能对话**\n"
                "• **语音/多轮对话**：像朋友一样聊天\n"
                "• **图片/视频分析**：发送媒体文件并提问\n"
                "• **网页摘要**：直接发送链接\n"
                "• **沉浸式翻译**：输入 /translate 开启\n\n"
                "**命令列表：**\n"
                "/stats - 使用统计\n"
                "/start - 主菜单\n"
                "/cancel - 取消\n\n"
                "遇到问题？直接问我！",
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
            
            await smart_edit_text(query.message,
                "⚙️ **设置**\n\n"
                "当前配置：\n"
                f"• Gemini 模型：{gemini_model}\n"
                f"• 画图模型：{image_model}\n"
                "• 视频质量：最高\n"
                "• 文件大小限制：49 MB\n\n"
                "更多设置功能即将推出...",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "platforms":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await smart_edit_text(query.message,
                "📊 **支持的视频平台**\n\n"
                "✅ X (Twitter) - twitter.com, x.com\n"
                "✅ YouTube - youtube.com, youtu.be\n"
                "✅ Instagram - instagram.com\n"
                "✅ TikTok - tiktok.com\n"
                "✅ Bilibili - bilibili.com\n\n"
                "支持绝大多数公开视频链接！",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "stats":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            from stats import get_user_stats_text
            user_id = query.from_user.id
            stats_text = await get_user_stats_text(user_id)
            
            await smart_edit_text(query.message,
                stats_text,
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
                    "📢 **我的订阅**\n\n"
                    "您还没有订阅任何内容。\n\n"
                    "**使用方法：**\n"
                    "• /subscribe `<URL>` : 订阅 RSS\n"
                    "• /monitor `<关键词>` : 监控新闻\n"
                )
            else:
                text = "📢 **我的订阅列表**\n\n"
                for sub in subs:
                    title = sub['title'] or '无标题'
                    url = sub['feed_url']
                    text += f"• [{title}]({url})\n"
                
                text += "\n使用 /unsubscribe `<URL>` 取消订阅。"
            
            await smart_edit_text(query.message,
                text,
                reply_markup=reply_markup
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
            
            status_text = "🌍 **已开启**" if new_status else "🚫 **已关闭**"
            desc = (
                "现在发送任何文本消息，我都会为您自动翻译。\n(外语->中文，中文->英文)" 
                if new_status else 
                "已恢复正常 AI 助手模式。"
            )
            
            await smart_edit_text(query.message,
                f"ℹ️ **沉浸式翻译模式**\n\n"
                f"当前状态：{status_text}\n\n"
                f"{desc}\n\n"
                "点击按钮可再次切换。",
                reply_markup=reply_markup
            )
            return ConversationHandler.END
            
        elif data == "remind_help":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await smart_edit_text(query.message,
                "⏰ **定时提醒使用帮助**\n\n"
                "请直接发送命令设置提醒：\n\n"
                "• **/remind 10m 关火** (10分钟后)\n"
                "• **/remind 1h30m 休息一下** (1小时30分后)\n\n"
                "时间单位支持：s(秒), m(分), h(时), d(天)",
                reply_markup=reply_markup
            )
            return ConversationHandler.END
            
        elif data == "back_to_main":
            # 重新显示主菜单
            reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())
            await smart_edit_text(query.message,
                WELCOME_MESSAGE,
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Error in button_callback for data {data}: {e}")
        # 尝试通知用户发生错误，如果 edit 失败
        try:
             await smart_reply_text(update, "❌ 操作失败，请重试或输入 /start 重启。")
        except:
             pass

    return ConversationHandler.END
