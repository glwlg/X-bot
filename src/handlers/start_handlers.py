import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from utils import smart_edit_text, smart_reply_text
from core.platform.models import UnifiedContext
from .base_handlers import check_permission_unified, check_permission

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "👋 **欢迎使用 X-Bot！**\n\n"
    "我是您的全能 AI 助手，支持 **自然语言交互** 与 **多模态分析**。\n\n"
    "💬 **直接对话**：你可以像朋友一样跟我聊天。\n"
    "🛠️ **执行任务**：下载视频、监控股票、阅读PDF、生成播客等。\n"
    "🧬 **自我进化**：遇到不会的问题，我会尝试自己写代码解决！\n\n"
    "👇 点击下方 **[ℹ️ 帮助]** 查看所有指令与技能。"
)

def get_main_menu_keyboard():
    return [
        [
            InlineKeyboardButton("ℹ️ 使用帮助 / Help", callback_data="help"),
        ],
    ]

async def start(ctx: UnifiedContext) -> None:
    """处理 /start 命令，显示欢迎消息和功能菜单"""
    if not await check_permission_unified(ctx):
        return

    reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())

    await ctx.reply(
        WELCOME_MESSAGE,
        reply_markup=reply_markup,
    )

async def handle_new_command(ctx: UnifiedContext) -> None:
    """处理 /new 命令，清空聊天上下文"""
    if not await check_permission_unified(ctx):
        return

    from user_context import clear_context
    # clear_context currently expects telegram context? 
    # Let's check user_context.py later. For now pass ctx.platform_ctx
    clear_context(ctx.platform_ctx)
    
    await ctx.reply(
        "🧹 **已开启新对话**\n\n"
        "之前的短期对话上下文已清空。\n"
        "不用担心，重要的长期记忆仍然保留！🧠"
    )

async def help_command(ctx: UnifiedContext) -> None:
    """处理 /help 命令"""
    if not await check_permission_unified(ctx):
        return
    
    await ctx.reply(
        "ℹ️ **X-Bot 使用指南**\n\n"
        "🚀 **多模态 AI**\n"
        "• **对话**：直接发送文本、语音。\n"
        "• **识图**：发送照片，问 \"这是什么\"。\n"
        "• **绘图**：\"画一只赛博朋克风格的猫\"。\n"
        "• **翻译**：使用 \"开启翻译模式\" 实现同声传译。\n\n"
        "📓 **NotebookLM 知识库**\n"
        "• **播客**：\"下载这个视频的播客\" 或 \"生成播客\"。\n"
        "• **问答**：\"询问 Kubernetes 调度原理\"。\n"
        "• **管理**：使用 \"NotebookLM\" 或 \"list notebooks\"。\n\n"
        "📹 **媒体下载**\n"
        "• 直接发送链接 (YouTube/X/B站等)，支持自动去重。\n"
        "• \"下载这个视频的音频 https://...\"\n\n"
        "📈 **行情与资讯**\n"
        "• \"帮我关注英伟达股票\"\n"
        "• \"监控关键词 AI\" (Google News)\n"
        "• \"订阅 RSS https://...\"\n\n"
        "⏰ **实用工具**\n"
        "• \"10分钟后提醒我喝水\"\n"
        "• \"部署这个仓库 https://...\"\n"
        "• \"列出运行的服务\"\n\n"
        "💡 **技能扩展 (自进化)**\n"
        "• **无师自通**：直接问我 \"查询最新 GitHub 趋势\"，我会自动学习新技能。\n"
        "• **手动教学**：/teach - 强制触发学习模式\n"
        "• /skills - 查看已安装技能\n\n"
        "**常用命令：**\n"
        "/start 主菜单 | /new 新对话 | /stats 统计"
    )

async def back_to_main_and_cancel(ctx: UnifiedContext) -> int:
    """返回主菜单并取消当前操作（用于在对话状态中）"""
    # Legacy fallback
    query = ctx.platform_event.callback_query
    await query.answer()
    
    logger.info("Back to main menu and cancel current operation")
    
    reply_markup = InlineKeyboardMarkup(get_main_menu_keyboard())
    
    try:
        await ctx.edit_message(
            query.message.message_id,
            WELCOME_MESSAGE,
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Error in back_to_main_and_cancel: {e}")
    
    return ConversationHandler.END

async def button_callback(ctx: UnifiedContext) -> int:
    """处理通用内联键盘按钮点击（非会话入口）"""
    if not await check_permission_unified(ctx):
        return ConversationHandler.END

    query = ctx.platform_event.callback_query
    msg_id = query.message.message_id
    data = query.data
    
    try:
        if data == "ai_chat":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await ctx.edit_message(msg_id,
                "💬 **AI 对话模式**\n\n"
                "现在您可以直接发送任何消息，我会用 AI 智能回复！\n\n"
                "💡 提示：直接在对话框输入消息即可，无需点击按钮。",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "help":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await ctx.edit_message(msg_id,
                "ℹ️ **X-Bot 使用指南**\n\n"
                "🚀 **多模态 AI**\n"
                "• **对话**：直接发送文本、语音。\n"
                "• **识图**：发送照片，问 \"这是什么\"。\n"
                "• **绘图**：\"画一只赛博朋克风格的猫\"。\n"
                "• **翻译**：使用 \"开启翻译模式\" 实现同声传译。\n\n"
                "• **绘图**：\"画一只赛博朋克风格的猫\"。\n"
                "• **翻译**：使用 \"开启翻译模式\" 实现同声传译。\n\n"
                "📓 **NotebookLM 知识库**\n"
                "• **播客**：\"下载这个视频的播客\" 或 \"生成播客\"。\n"
                "• **问答**：\"询问 Kubernetes 调度原理\"。\n"
                "• **管理**：使用 \"NotebookLM\" 或 \"list notebooks\"。\n\n"
                "📹 **媒体下载**\n"
                "• 直接发送链接 (YouTube/X/B站等)，支持自动去重。\n"
                "• \"下载这个视频的音频 https://...\"\n\n"
                "📈 **行情与资讯**\n"
                "• \"帮我关注英伟达股票\"\n"
                "• \"监控关键词 AI\" (Google News)\n"
                "• \"订阅 RSS https://...\"\n\n"
                "⏰ **实用工具**\n"
                "• \"10分钟后提醒我喝水\"\n"
                "• \"部署这个仓库 https://...\"\n"
                "• \"列出运行的服务\"\n\n"
                "💡 **技能扩展**\n"
                "• /teach - 教我学会新技能 (自定义代码)\n"
                "• /skills - 查看已安装技能\n\n"
                "**常用命令：**\n"
                "/start 主菜单 | /new 新对话 | /stats 统计",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "settings":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # 安全获取环境变量
            openai_model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
            gemini_model = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
            
            await ctx.edit_message(msg_id,
                "⚙️ **设置**\n\n"
                "当前配置：\n"
                f"• Gemini 模型：{gemini_model}\n"
                "• 视频质量：最高\n"
                "• 文件大小限制：49 MB\n\n"
                "更多设置功能即将推出...",
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "platforms":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await ctx.edit_message(msg_id,
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
            user_id = ctx.message.user.id
            stats_text = await get_user_stats_text(user_id)
            
            await ctx.edit_message(msg_id,
                stats_text,
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
        
        elif data == "watchlist":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            user_id = ctx.message.user.id
            from repositories import get_user_watchlist
            from services.stock_service import fetch_stock_quotes, format_stock_message
            
            watchlist = await get_user_watchlist(user_id)
            
            if not watchlist:
                text = (
                    "📈 **我的自选股**\n\n"
                    "您还没有添加自选股。\n\n"
                    "**使用方法：**\n"
                    "• 发送「帮我关注仙鹤股份」添加\n"
                    "• 支持多只：「关注红太阳和联环药业」\n"
                    "• /watchlist 查看列表"
                )
            else:
                stock_codes = [item["stock_code"] for item in watchlist]
                quotes = await fetch_stock_quotes(stock_codes)
                
                if quotes:
                    text = format_stock_message(quotes)
                else:
                    lines = ["📈 **我的自选股**\n"]
                    for item in watchlist:
                        lines.append(f"• {item['stock_name']} ({item['stock_code']})")
                    text = "\n".join(lines)
                
                text += "\n\n发送「取消关注 XX」可删除"
            
            await ctx.edit_message(msg_id, text, reply_markup=reply_markup)
            return ConversationHandler.END
        
        elif data == "list_subs":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            user_id = ctx.message.user.id
            from repositories import get_user_subscriptions
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
            
            await ctx.edit_message(msg_id,
                text,
                reply_markup=reply_markup
            )
            return ConversationHandler.END
            
        elif data == "toggle_translation":
            keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            user_id = ctx.message.user.id
            from repositories import get_user_settings, set_translation_mode
            
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
            
            await ctx.edit_message(msg_id,
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
            
            await ctx.edit_message(msg_id,
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
            await ctx.edit_message(msg_id,
                WELCOME_MESSAGE,
                reply_markup=reply_markup,
            )
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Error in button_callback for data {data}: {e}")
        # 尝试通知用户发生错误，如果 edit 失败
        try:
             await ctx.reply("❌ 操作失败，请重试或输入 /start 重启。")
        except:
             pass

    return ConversationHandler.END
