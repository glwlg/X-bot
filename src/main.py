"""
X-Bot: 多平台媒体助手 + AI 智能伙伴
主程序入口 - Unified Asyncio Version
"""

import logging
import asyncio
import signal
from telegram.ext import (
    Application,
    Application,
    ContextTypes,
    ConversationHandler,
    PicklePersistence,
    filters,
    TypeHandler,
)
from telegram import Update

from core.config import (
    TELEGRAM_BOT_TOKEN,
    DISCORD_BOT_TOKEN,
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
    LOG_LEVEL,
    WAITING_FOR_FEATURE_INPUT,
)
from handlers import (
    start,
    handle_new_command,
    help_command,
    button_callback,
    button_callback,
    back_to_main_and_cancel,
    cancel,
    stats_command,
    handle_ai_chat,
    handle_ai_photo,
    handle_ai_video,
    handle_sticker_message,
    feature_command,
    handle_feature_input,
    save_feature_command,
    toggle_translation_command,
    stop_command,
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

# Multi-Channel Imports
from core.platform.registry import adapter_manager
from core.platform.models import MessageType
from platforms.telegram.adapter import TelegramAdapter
from platforms.discord.adapter import DiscordAdapter

# 日志配置
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)


logger = logging.getLogger(__name__)


async def init_services() -> None:
    """初始化全局服务（数据库、调度器、Skills等）"""
    logger.info("⚡ Initializing global services...")
    try:
        from repositories import init_db

        await init_db()
        logger.info("✅ Database initialized.")

        # 加载待执行的提醒任务
        from core.scheduler import (
            scheduler,
            load_jobs_from_db,
            start_rss_scheduler,
            start_stock_scheduler,
            start_dynamic_skill_scheduler,
        )

        logger.info("⚡ Starting schedulers...")
        # Start APScheduler
        scheduler.start()

        # Initialize Jobs
        await load_jobs_from_db()
        # 启动 RSS 检查
        start_rss_scheduler()
        # 启动股票盯盘推送
        start_stock_scheduler()
        # 启动动态 Skill 定时任务
        start_dynamic_skill_scheduler()
        logger.info("✅ Schedulers started.")

        # 初始化 Skill 索引
        from core.skill_loader import skill_loader

        skill_loader.scan_skills()
        logger.info(f"Loaded {len(skill_loader.get_skill_index())} skills")
    except Exception as e:
        logger.error(f"❌ Error in init_services: {e}", exc_info=True)

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


async def setup_telegram_commands(application: Application) -> None:
    """Register Telegram Commands"""
    await application.bot.set_my_commands(
        [
            ("start", "主菜单"),
            ("new", "开启新对话"),
            ("teach", "教我新能力"),
            ("skills", "查看 Skills"),
            ("feature", "提交需求"),
            ("stats", "使用统计"),
            ("translate", "沉浸式翻译"),
            ("help", "使用帮助"),
            ("cancel", "取消当前操作"),
        ]
    )


async def log_update(update: Update, context):
    """记录所有收到的 Update，用于调试"""
    if update.callback_query:
        logger.info(
            f"👉 RECEIVED CALLBACK: {update.callback_query.data} from user {update.effective_user.id}"
        )
    elif update.message:
        logger.info(
            f"📩 RECEIVED MESSAGE: {update.message.text} from user {update.effective_user.id}"
        )


async def main():
    """Universal Main Entry Point"""
    logger.info("Starting X-Bot (Universal Mode)...")

    # 1. Setup Telegram Application
    # 调整第三方库日志级别，避免刷屏 (Moved to main for reliability)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    tg_app = None
    tg_adapter = None
    if TELEGRAM_BOT_TOKEN:
        persistence = PicklePersistence(filepath="data/bot_persistence.pickle")
        tg_app = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .persistence(persistence)
            .concurrent_updates(True)
            .read_timeout(60)
            .write_timeout(120)
            .build()
        )

        # Debug logging
        tg_app.add_handler(TypeHandler(Update, log_update), group=-1)

        # 2. Setup Adapters
        # A. Telegram Adapter
        tg_adapter = TelegramAdapter(tg_app)
        adapter_manager.register_adapter(tg_adapter)
        logger.info("✅ Telegram Adapter enabled.")
    else:
        logger.info("ℹ️ Telegram Adapter skipped (no token).")

    # --- Global Initialization (Decoupled from TG) ---
    await init_services()

    # if tg_app:
    #     await setup_telegram_commands(tg_app)
    # -----------------------------------------------

    # B. Discord Adapter
    if DISCORD_BOT_TOKEN:
        discord_adapter = DiscordAdapter(DISCORD_BOT_TOKEN)
        adapter_manager.register_adapter(discord_adapter)
        logger.info("✅ Discord Adapter enabled.")
    else:
        logger.info("ℹ️ Discord Adapter skipped (no token).")

    # C. DingTalk Adapter
    if DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET:
        from platforms.dingtalk.adapter import DingTalkAdapter

        dingtalk_adapter = DingTalkAdapter(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET)
        adapter_manager.register_adapter(dingtalk_adapter)
        logger.info("✅ DingTalk Adapter enabled (Stream Mode).")
    else:
        logger.info("ℹ️ DingTalk Adapter skipped (missing credentials).")

    # 3. Register Handlers (Unified)
    # Broadcast common commands
    # Broadcast common commands
    adapter_manager.on_command("start", start, description="显示主菜单")
    adapter_manager.on_command("new", handle_new_command, description="开启新对话")
    adapter_manager.on_command("help", help_command, description="使用帮助")
    adapter_manager.on_command("stats", stats_command, description="查看统计信息")
    adapter_manager.on_command("skills", skills_command, description="查看可用技能")
    adapter_manager.on_command(
        "reload_skills", reload_skills_command, description="重载技能"
    )
    adapter_manager.on_command(
        "translate", toggle_translation_command, description="开启/关闭沉浸式翻译"
    )
    adapter_manager.on_command("stop", stop_command, description="停止当前任务")

    # ----------------------------------------------
    # 3.1 DYNAMIC SKILL HANDLER REGISTRATION
    # ----------------------------------------------
    from core.skill_loader import skill_loader

    logger.info("🔌 Registering dynamic skill handlers...")
    skill_loader.register_skill_handlers(adapter_manager)
    # ----------------------------------------------

    # 4. Register Platform-Specific Handlers (Telegram Complex Flows)
    if tg_adapter:
        # Telegram Buttons & Callbacks

        common_pattern = "^(?!back_to_main_cancel$|unsub_|stock_|skill_|del_rss_|del_stock_|action_).*$"
        tg_adapter.on_callback_query(common_pattern, button_callback)
        tg_adapter.on_callback_query("^skill_", handle_skill_callback)
        # Note: stock_ & unsub_ are now registered via register_skill_handlers dynamically

        # Telegram Conversations

        # Video Download Handler moved to skills/builtin/download_video

        feature_conv_handler = ConversationHandler(
            entry_points=[
                tg_adapter.create_command_handler("feature", feature_command)
            ],
            states={
                WAITING_FOR_FEATURE_INPUT: [
                    tg_adapter.create_command_handler(
                        "save_feature", save_feature_command
                    ),
                    tg_adapter.create_message_handler(
                        filters.TEXT & ~filters.COMMAND, handle_feature_input
                    ),
                ],
            },
            fallbacks=[
                tg_adapter.create_command_handler("cancel", cancel),
                tg_adapter.create_command_handler("save_feature", save_feature_command),
            ],
            per_message=False,
        )
        tg_app.add_handler(feature_conv_handler)

        teach_conv_handler = ConversationHandler(
            entry_points=[tg_adapter.create_command_handler("teach", teach_command)],
            states={
                WAITING_FOR_SKILL_DESC: [
                    tg_adapter.create_message_handler(
                        filters.TEXT & ~filters.COMMAND, handle_teach_input
                    )
                ],
            },
            fallbacks=[tg_adapter.create_command_handler("cancel", cancel)],
            per_message=False,
        )
        tg_app.add_handler(teach_conv_handler)

        # 5. Media Handlers
        tg_adapter.on_message(filters.PHOTO, handle_ai_photo)
        tg_adapter.on_message(filters.VIDEO, handle_ai_video)
        tg_adapter.on_message(filters.VOICE | filters.AUDIO, handle_voice_message)
        tg_adapter.on_message(filters.Document.ALL, handle_document)
        tg_adapter.on_message(filters.Sticker.ALL, handle_sticker_message)
        tg_adapter.on_message(filters.TEXT & ~filters.COMMAND, handle_ai_chat)
    else:
        pass

    # Register Discord equivalents (Manual mapping for now)
    if DISCORD_BOT_TOKEN:

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
                await handle_ai_chat(ctx)

        discord_adapter.register_message_handler(discord_router)

        # Register Discord Callbacks (Unified)
        discord_adapter.on_callback_query("^skill_", handle_skill_callback)
        # unsubs, stock Handled by dynamic

        # Generic Button Callback (Help, Settings, etc.)
        # Note: Discord regex matching might be slightly different if compiled differently, but standard python re works.
        # We reuse the common pattern from Telegram.
        # Generic Button Callback (Help, Settings, etc.)
        # Note: Discord regex matching might be slightly different if compiled differently, but standard python re works.
        # We reuse the common pattern from Telegram.
        common_pattern = "^(?!back_to_main_cancel$|unsub_|stock_|skill_|del_rss_|del_stock_|action_).*$"
        discord_adapter.on_callback_query(common_pattern, button_callback)

        # Note: ConversationHandler logic not yet fully ported to DiscordAdapter
        # So /download command state machine won't work perfectly on Discord yet
        # But stateless actions will.

    # Register DingTalk handlers
    if DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET:

        async def dingtalk_router(ctx):
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
                await handle_ai_chat(ctx)

        dingtalk_adapter.register_message_handler(dingtalk_router)

        # Register DingTalk Callbacks (Unified)
        dingtalk_adapter.on_callback_query("^skill_", handle_skill_callback)

        # Generic Button Callback
        # Generic Button Callback
        common_pattern = "^(?!back_to_main_cancel$|unsub_|stock_|skill_|del_rss_|del_stock_|action_).*$"
        dingtalk_adapter.on_callback_query(common_pattern, button_callback)

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
