"""
X-Bot: 多平台媒体助手 + AI 智能伙伴
主程序入口
"""
import logging
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    PicklePersistence,
    filters,
)
from telegram import Update

from core.config import (
    TELEGRAM_BOT_TOKEN,
    WAITING_FOR_VIDEO_URL,
    WAITING_FOR_REMIND_INPUT,
    WAITING_FOR_MONITOR_KEYWORD,
    WAITING_FOR_SUBSCRIBE_URL,
    WAITING_FOR_FEATURE_INPUT,
)
from handlers import (
    start,
    handle_new_command,
    help_command,
    adduser_command,
    deluser_command,
    button_callback,
    start_download_video,
    back_to_main_and_cancel,
    handle_download_format,
    download_command,
    handle_video_download,
    cancel,
    handle_large_file_action,
    remind_command,
    handle_remind_input,
    handle_unsubscribe_callback,
    handle_monitor_input,
    handle_video_actions,
    stats_command,
    handle_ai_chat, 
    handle_ai_photo, 
    handle_ai_video,
    feature_command,
    handle_feature_input,
    save_feature_command,
    handle_stock_select_callback,
)
from handlers.voice_handler import handle_voice_message
from handlers.document_handler import handle_document
from handlers.skill_handlers import (
    teach_command,
    handle_teach_input,
    handle_skill_callback,
    skills_command,
    reload_skills_command,
    WAITING_FOR_SKILL_DESC,
)
from handlers.callback_handlers import handle_subscription_callback
from handlers.deployment_handlers import deploy_command

# Multi-Channel Imports
from core.platform.registry import AdapterManager
from platforms.telegram.adapter import TelegramAdapter


# 日志配置
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)



async def initialize_data(application: Application) -> None:
    """初始化数据（数据库等）和设置菜单"""
    from repositories import init_db
    await init_db()
    
    
    # 加载待执行的提醒任务
    from handlers.subscription_handlers import refresh_user_subscriptions
    from core.scheduler import load_jobs_from_db, start_rss_scheduler, start_stock_scheduler
    await load_jobs_from_db(application.job_queue)
    
    # 启动 RSS 检查
    start_rss_scheduler(application.job_queue)
    
    # 启动股票盯盘推送
    start_stock_scheduler(application.job_queue)
    
    # 初始化 Skill 索引
    from core.skill_loader import skill_loader
    skill_loader.scan_skills()
    logger.info(f"Loaded {len(skill_loader.get_skill_index())} skills")
    
    # Pre-connect MCP Memory for Admin to reduce latency
    from core.config import ADMIN_USER_IDS
    from mcp_client.manager import mcp_manager
    from mcp_client.memory import MemoryMCPServer
    
    # Register the memory server class
    mcp_manager.register_server_class("memory", MemoryMCPServer)
    
    if ADMIN_USER_IDS:
        admin_id = list(ADMIN_USER_IDS)[0]
        logger.info(f"🚀 Pre-connecting MCP Memory for Admin: {admin_id}")
        # Build logic in background to not block startup significantly? 
        # Actually we want it ready.
        try:
            # We call get_server which auto-connects
            await mcp_manager.get_server("memory", user_id=admin_id)
            logger.info("✅ MCP Memory pre-connected.")
        except Exception as e:
            logger.error(f"⚠️ MCP Pre-connect failed: {e}")

    await application.bot.set_my_commands(
        [
            ("start", "主菜单"),
            ("new", "开启新对话"),
            ("new", "开启新对话"),
            ("teach", "教我新能力"),            ("teach", "教我新能力"),
            ("skills", "查看 Skills"),
            ("feature", "提交需求"),
            ("stats", "使用统计"),
            ("help", "使用帮助"),
            ("cancel", "取消当前操作"),
        ]
    )
    
    # 删除 setup_bot_menu 函数，合并到这里


async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """记录所有收到的 Update，用于调试"""
    if update.callback_query:
        logger.info(f"👉 RECEIVED CALLBACK: {update.callback_query.data} from user {update.effective_user.id}")
    elif update.message:
        logger.info(f"📩 RECEIVED MESSAGE: {update.message.text} from user {update.effective_user.id}")


def main() -> None:
    """启动 Bot"""
    logger.info("Starting X - Bot...")

    # 配置持久化存储
    persistence = PicklePersistence(filepath="data/bot_persistence.pickle")

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .read_timeout(60)
        .write_timeout(120)
        .build()
    )

    # 设置 Bot 初始化 (加载数据库和菜单)
    application.post_init = initialize_data

    # --- Multi-Channel Adapter Setup ---
    adapter_manager = AdapterManager()
    tg_adapter = TelegramAdapter(application)
    adapter_manager.register_adapter(tg_adapter)
    # -----------------------------------

    # 0. 全局调试记录器 (注册在最前面)
    from telegram.ext import TypeHandler
    application.add_handler(TypeHandler(Update, log_update), group=-1)

    # 1. 独立注册通用按钮 (保证这些按钮永远可点，不受会话状态影响)
    # 处理 help, settings, platforms, back_to_main, ai_chat
    # 注意：排除 download_video, generate_image, back_to_main_cancel 以及 dl_format_ 和 large_file_ 开头的回调
    
    # 1.0 先注册智能视频操作按钮 (优先级高于通用按钮)
    tg_adapter.on_callback_query("^action_.*", handle_video_actions)

    # 1.1 大文件处理按钮
    tg_adapter.on_callback_query("^large_file_", handle_large_file_action)
    
    # 1.2 通用菜单按钮
    common_pattern = "^(?!download_video$|back_to_main_cancel$|dl_format_|large_file_|action_|unsub_|stock_|skill_|del_rss_|del_stock_).*$"
    # [UNIFIED]
    tg_adapter.on_callback_query(common_pattern, button_callback)
    
    # 1.3 Skill 审核按钮
    # [UNIFIED]
    tg_adapter.on_callback_query("^skill_", handle_skill_callback)
    
    # Handler for subscription management (delete)
    application.add_handler(CallbackQueryHandler(handle_subscription_callback, pattern="^(del_rss_|del_stock_)"))

    # AI Chat Handler (Text)
    # [UNIFIED] Factory for conversation handler
    back_handler = tg_adapter.create_callback_handler("^back_to_main_cancel$", back_to_main_and_cancel)
    format_handler = tg_adapter.create_callback_handler("^dl_format_", handle_download_format)
    # Note: start_download_video is triggered by button, so use callback handler as entry point
    # But CallbackQueryHandler usually doesn't take 'pattern' inside create_... (wait, my factory supports pattern?)
    # Let's check adapter.py. 
    # create_callback_handler(self, pattern: str, handler_func: Callable) -> CallbackQueryHandler
    
    video_conv_handler = ConversationHandler(
        entry_points=[
            tg_adapter.create_callback_handler("^download_video$", start_download_video),
            tg_adapter.create_command_handler("download", download_command),
        ],
        states={
            WAITING_FOR_VIDEO_URL: [
                back_handler,
                format_handler,
                tg_adapter.create_message_handler(filters.TEXT & ~filters.COMMAND, handle_video_download),
            ],
        },
        fallbacks=[tg_adapter.create_command_handler("cancel", cancel), back_handler, format_handler],

        allow_reentry=True,
        per_message=False,
    )
    

    # 3.4 需求收集对话处理器
    feature_conv_handler = ConversationHandler(
        entry_points=[tg_adapter.create_command_handler("feature", feature_command)],
        states={
            WAITING_FOR_FEATURE_INPUT: [
                tg_adapter.create_command_handler("save_feature", save_feature_command),
                tg_adapter.create_message_handler(filters.TEXT & ~filters.COMMAND, handle_feature_input)
            ],
        },

        fallbacks=[tg_adapter.create_command_handler("cancel", cancel), tg_adapter.create_command_handler("save_feature", save_feature_command)],
        per_message=False,
    )

    # 4. 注册核心功能处理器
    # [UNIFIED] 使用 Adapter 注册统一命令
    tg_adapter.on_command("start", start)
    tg_adapter.on_command("help", help_command)
    tg_adapter.on_command("new", handle_new_command)
    
    # [LEGACY] 传统方式注册
    tg_adapter.on_command("adduser", adduser_command)
    tg_adapter.on_command("deluser", deluser_command)
    tg_adapter.on_command("deploy", deploy_command)

    
    # 移除独立命令注册 (已迁移至 Skill)
    # remind, translate, subscribe, monitor, watchlist
    
    # 4.1 核心后台回调 (Skill 可能触发)
    # [UNIFIED] 使用 Adapter 注册回调
    tg_adapter.on_callback_query("^unsub_", handle_unsubscribe_callback)
    tg_adapter.on_callback_query("^stock_", handle_stock_select_callback)
    
    # 4.2 特色功能
    application.add_handler(feature_conv_handler)
    tg_adapter.on_command("stats", stats_command)
    application.add_handler(video_conv_handler)
    
    # 4.1 Skill 管理命令
    teach_conv_handler = ConversationHandler(
        entry_points=[tg_adapter.create_command_handler("teach", teach_command)],
        states={
            WAITING_FOR_SKILL_DESC: [
                tg_adapter.create_message_handler(filters.TEXT & ~filters.COMMAND, handle_teach_input)
            ],
        },

        fallbacks=[tg_adapter.create_command_handler("cancel", cancel)],
        per_message=False,
    )
    application.add_handler(teach_conv_handler)
    tg_adapter.on_command("skills", skills_command)
    tg_adapter.on_command("reload_skills", reload_skills_command)
    
    # 5. 图片消息处理器（AI 图片分析）
    tg_adapter.on_message(filters.PHOTO, handle_ai_photo)
    
    # 6. 视频消息处理器（AI 视频分析）
    tg_adapter.on_message(filters.VIDEO, handle_ai_video)
    
    # 7. 语音/音频消息处理器（包括 voice 和 audio）
    tg_adapter.on_message(filters.VOICE | filters.AUDIO, handle_voice_message)
    
    # 8. 文档消息处理器（PDF、DOCX）
    tg_adapter.on_message(filters.Document.ALL, handle_document)
    
    # 9. AI 对话处理器（兜底文本消息）
    tg_adapter.on_message(filters.TEXT & ~filters.COMMAND, handle_ai_chat)

    # 启动 Bot
    logger.info("Bot is running...")
    application.run_polling(
        allowed_updates=["message", "callback_query", "edited_message"]
    )


if __name__ == "__main__":
    main()
