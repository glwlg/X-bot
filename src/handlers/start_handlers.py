import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from .base_handlers import check_permission

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "👋 <b>欢迎使用 X-Bot！</b>\n\n"
    "我不仅仅是一个机器人，更是您的智能 AI 伙伴。🧠\n"
    "<b>现在支持自然语言指令！试着对我发：</b>\n\n"
    "📥 <b>下载</b>\n"
    "• \"帮我下载这个视频 https://...\"\n"
    "• \"保存这段音频 https://...\"\n\n"
    "🎨 <b>创作</b>\n"
    "• \"画一只在太空的猫\"\n\n"
    "⏰ <b>生活</b>\n"
    "• \"10分钟后提醒我喝水\"\n"
    "• \"订阅这个RSS源 https://...\"\n"
    "• \"监控关键词 AI News\"\n\n"
    "💬 <b>对话</b>\n"
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
                "🚀 <b>新功能：自然语言指令</b>\n"
                "无需死记硬背命令，直接对我说话即可！\n"
                "• \"下载视频 https://...\"\n"
                "• \"画一张赛博朋克风格的图\"\n"
                "• \"1小时后提醒我开会\"\n"
                "• \"监控关键词 DeepSeek\"\n"
                "• \"订阅这个RSS https://...\"\n\n"
                "<b>🤖 AI 智能对话</b>\n"
                "• <b>语音/多轮对话</b>：像朋友一样聊天\n"
                "• <b>图片/视频分析</b>：发送媒体文件并提问\n"
                "• <b>文档分析</b>：发送 PDF/Word 文档\n"
                "• <b>网页摘要</b>：直接发送链接\n"
                "• <b>沉浸式翻译</b>：输入 /translate 开启\n\n"
                "<b>命令列表：</b>\n"
                "/stats - 使用统计\n"
                "/start - 主菜单\n"
                "/cancel - 取消\n\n"
                "遇到问题？直接问我！",
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
