"""
X-Bot: 多平台媒体助手 + AI 智能伙伴
主程序入口 - Unified Asyncio Version
"""
import logging
import asyncio
import signal
import sys
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    PicklePersistence,
    filters,
    TypeHandler
)
from telegram import Update

from core.config import (
    TELEGRAM_BOT_TOKEN,
    DISCORD_BOT_TOKEN,
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
    handle_stock_select_callback,
    handle_monitor_input,
    handle_video_actions,
    stats_command,
    handle_ai_chat, 
    handle_ai_photo, 
    handle_ai_video,
    feature_command,
    handle_feature_input,
    save_feature_command,
)
from handlers.skill_handlers import (
    teach_command,
    handle_teach_input,
    handle_skill_callback,
    skills_command,
    reload_skills_command,
    WAITING_FOR_SKILL_DESC,
)
from handlers.voice_handler import handle_voice_message
from handlers.document_handler import handle_document
from handlers.callback_handlers import handle_subscription_callback
from handlers.deployment_handlers import deploy_command

# Multi-Channel Imports
from core.platform.registry import AdapterManager
from platforms.telegram.adapter import TelegramAdapter
from platforms.discord.adapter import DiscordAdapter

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
    from core.scheduler import load_jobs_from_db, start_rss_scheduler, start_stock_scheduler
    # Ensure job_queue is ready (it should be after application.initialize)
    if application.job_queue:
        await load_jobs_from_db(application.job_queue)
        # 启动 RSS 检查
        start_rss_scheduler(application.job_queue)
        # 启动股票盯盘推送
        start_stock_scheduler(application.job_queue)
    
    # 初始化 Skill 索引
    from core.skill_loader import skill_loader
    skill_loader.scan_skills()
    logger.info(f"Loaded {len(skill_loader.get_skill_index())} skills")
    
    # Pre-connect MCP Memory for Admin
    from core.config import ADMIN_USER_IDS
    from mcp_client.manager import mcp_manager
    from mcp_client.memory import MemoryMCPServer
    
    mcp_manager.register_server_class("memory", MemoryMCPServer)
    
    if ADMIN_USER_IDS:
        admin_id = list(ADMIN_USER_IDS)[0]
        logger.info(f"🚀 Pre-connecting MCP Memory for Admin: {admin_id}")
        try:
            await mcp_manager.get_server("memory", user_id=admin_id)
            logger.info("✅ MCP Memory pre-connected.")
        except Exception as e:
            logger.error(f"⚠️ MCP Pre-connect failed: {e}")

    # Set Telegram specific commands
    await application.bot.set_my_commands(
        [
            ("start", "主菜单"),
            ("new", "开启新对话"),
            ("teach", "教我新能力"),
            ("skills", "查看 Skills"),
            ("feature", "提交需求"),
            ("stats", "使用统计"),
            ("help", "使用帮助"),
            ("cancel", "取消当前操作"),
        ]
    )


async def log_update(update: Update, context):
    """记录所有收到的 Update，用于调试"""
    if update.callback_query:
        logger.info(f"👉 RECEIVED CALLBACK: {update.callback_query.data} from user {update.effective_user.id}")
    elif update.message:
        logger.info(f"📩 RECEIVED MESSAGE: {update.message.text} from user {update.effective_user.id}")


async def main():
    """Universal Main Entry Point"""
    logger.info("Starting X-Bot (Universal Mode)...")

    # 1. Setup Telegram Application
    tg_adapter = None
    if TELEGRAM_BOT_TOKEN:
        persistence = PicklePersistence(filepath="data/bot_persistence.pickle")
        tg_app = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .persistence(persistence)
            .read_timeout(60)
            .write_timeout(120)
            .build()
        )
        
        # Hook initialization
        tg_app.post_init = initialize_data
        
        # Debug logging
        tg_app.add_handler(TypeHandler(Update, log_update), group=-1)

        # 2. Setup Adapters
        adapter_manager = AdapterManager()
        
        # A. Telegram Adapter
        tg_adapter = TelegramAdapter(tg_app)
        adapter_manager.register_adapter(tg_adapter)
        logger.info("✅ Telegram Adapter enabled.")
    else:
        logger.info("ℹ️ Telegram Adapter skipped (no token).")
        # Initialize basic components that TG init used to do if TG is missing?
        # e.g., DB init needs to run somewhere. 
        # For now, simplistic approach: TG is primary, if no TG, we might miss some init unless we refactor initialize_data
        # Let's ensure adapter_manager is created
        adapter_manager = AdapterManager()
    
    # B. Discord Adapter
    if DISCORD_BOT_TOKEN:
        discord_adapter = DiscordAdapter(DISCORD_BOT_TOKEN)
        adapter_manager.register_adapter(discord_adapter)
        logger.info("✅ Discord Adapter enabled.")
    else:
        logger.info("ℹ️ Discord Adapter skipped (no token).")

    # 3. Register Handlers (Unified)
    # Broadcast common commands
    adapter_manager.on_command("start", start)
    adapter_manager.on_command("help", help_command)
    adapter_manager.on_command("new", handle_new_command)
    adapter_manager.on_command("stats", stats_command)
    adapter_manager.on_command("skills", skills_command)
    adapter_manager.on_command("reload_skills", reload_skills_command)
    
    # Legacy/Admin commands (Broadcast to all? Or just TG?)
    # For now broadcast, assuming adapter handles permission checks inside handler if generic
    adapter_manager.on_command("deploy", deploy_command) 
    
    # 4. Register Platform-Specific Handlers (Telegram Complex Flows)
    # These rely on python-telegram-bot features (ConversationHandler)
    # So we attach them directly to tg_adapter/application logic via existing code
    
    if tg_adapter:
        # Telegram Buttons & Callbacks
        tg_adapter.on_callback_query("^action_.*", handle_video_actions)
        tg_adapter.on_callback_query("^large_file_", handle_large_file_action)
        
        common_pattern = "^(?!download_video$|back_to_main_cancel$|dl_format_|large_file_|action_|unsub_|stock_|skill_|del_rss_|del_stock_).*$"
        tg_adapter.on_callback_query(common_pattern, button_callback)
        tg_adapter.on_callback_query("^skill_", handle_skill_callback)
        tg_adapter.on_callback_query("^unsub_", handle_unsubscribe_callback)
        tg_adapter.on_callback_query("^stock_", handle_stock_select_callback)
        tg_app.add_handler(CallbackQueryHandler(handle_subscription_callback, pattern="^(del_rss_|del_stock_)"))

        # Telegram Conversations
        back_handler = tg_adapter.create_callback_handler("^back_to_main_cancel$", back_to_main_and_cancel)
        format_handler = tg_adapter.create_callback_handler("^dl_format_", handle_download_format)
        
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
        tg_app.add_handler(video_conv_handler)
        
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
        tg_app.add_handler(feature_conv_handler)

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
        tg_app.add_handler(teach_conv_handler)
        
        # 5. Media Handlers (Register to AdapterManager for broadcast where supported)
        # Telegram supports Photo/Video/Voice/Doc specific filters.
        # Discord adapter maps these to Message + Type check.
        # We should add `on_message` support to AdapterManager to broadcast generic content handlers.
        
        # For now, register to TG explicitly, and Discord explicitly if simple
        tg_adapter.on_message(filters.PHOTO, handle_ai_photo)
        tg_adapter.on_message(filters.VIDEO, handle_ai_video)
        tg_adapter.on_message(filters.VOICE | filters.AUDIO, handle_voice_message)
        tg_adapter.on_message(filters.Document.ALL, handle_document)
        tg_adapter.on_message(filters.TEXT & ~filters.COMMAND, handle_ai_chat)
    else:
         # Need to init DB if Telegram is not present, because initialize_data was bound to tg_app.post_init
         # We need a manual init here.
         from repositories import init_db
         await init_db()
         
         # Load skills
         from core.skill_loader import skill_loader
         skill_loader.scan_skills()
         logger.info(f"Loaded {len(skill_loader.get_skill_index())} skills")

    # Register Discord equivalents (Manual mapping for now)
    if DISCORD_BOT_TOKEN:
         # DiscordAdapter handles type internally in `_map_message` and we can route in `on_message` adapter implementation?
         # Or we register a general message handler to Discord and let it delegate?
         # DiscordAdapter.register_message_handler takes (ctx).
         
         # We need a router for Discord that checks type/content and calls appropriate UC handler.
         from core.platform.models import MessageType
         
         async def discord_router(ctx):
             msg_type = ctx.message.type
             if msg_type == MessageType.IMAGE:
                 await handle_ai_photo(ctx)
             elif msg_type == MessageType.VIDEO:
                 await handle_ai_video(ctx)
             elif msg_type == MessageType.AUDIO or msg_type == MessageType.VOICE:
                 await handle_voice_message(ctx)
             elif msg_type == MessageType.DOCUMENT:
                 await handle_document(ctx)
             else:
                 # Text or Unknown
                 await handle_ai_chat(ctx)
                 
                  
         discord_adapter.register_message_handler(discord_router)

         # Register Discord Callbacks (Unified)
         discord_adapter.on_callback_query("^action_.*", handle_video_actions)
         discord_adapter.on_callback_query("^skill_", handle_skill_callback)
         discord_adapter.on_callback_query("^unsub_", handle_unsubscribe_callback)
         discord_adapter.on_callback_query("^stock_", handle_stock_select_callback)
         
         # Generic Button Callback (Help, Settings, etc.)
         # Note: Discord regex matching might be slightly different if compiled differently, but standard python re works.
         # We reuse the common pattern from Telegram.
         common_pattern = "^(?!download_video$|back_to_main_cancel$|dl_format_|large_file_|action_|unsub_|stock_|skill_|del_rss_|del_stock_).*$"
         discord_adapter.on_callback_query(common_pattern, button_callback)
         
         # Note: ConversationHandler logic not yet fully ported to DiscordAdapter
         # So /download command state machine won't work perfectly on Discord yet
         # But stateless actions will.

    # 6. Start Engines
    stop_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info(f"Signal {signum} received, stopping...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await adapter_manager.start_all()
        
        # Keep alive
        logger.info("All adapters started. Press Ctrl+C to stop.")
        await stop_event.wait()
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Shutting down...")
        await adapter_manager.stop_all()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
