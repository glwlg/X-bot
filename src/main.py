"""
DLP Bot - X-Bot: 多平台媒体助手 + AI 智能伙伴
主程序入口
"""
import logging
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ConversationHandler,
    filters,
)
from telegram import Update

from config import TELEGRAM_BOT_TOKEN, WAITING_FOR_VIDEO_URL, WAITING_FOR_IMAGE_PROMPT
from handlers import (
    start,
    button_callback,
    start_download_video,
    start_generate_image,
    back_to_main_and_cancel,
    handle_download_format,
    download_command,
    handle_video_download,
    image_command,
    handle_image_prompt,
    image_command,
    handle_image_prompt,
    image_command,
    handle_image_prompt,
    cancel,
    handle_large_file_action,
    remind_command,
    remind_command,
    toggle_translation_command,
    subscribe_command,
    unsubscribe_command,
    list_subs_command,
    monitor_command,
)
from ai_handler import handle_ai_chat, handle_ai_photo, handle_ai_video
from voice_handler import handle_voice_message
from document_handler import handle_document

# 日志配置
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)



async def initialize_data(application: Application) -> None:
    """初始化数据（数据库等）和设置菜单"""
    from database import init_db
    await init_db()
    
    
    # 加载待执行的提醒任务
    # 加载待执行的提醒任务
    from scheduler import load_jobs_from_db, start_rss_scheduler
    await load_jobs_from_db(application.job_queue)
    
    # 启动 RSS 检查
    start_rss_scheduler(application.job_queue)

    await application.bot.set_my_commands(
        [
            ("start", "主菜单"),
            ("download", "下载视频"),
            ("remind", "设置提醒"),
            ("translate", "沉浸式翻译(开关)"),
            ("monitor", "监控关键词"),
            ("subscribe", "订阅 RSS"),
            ("list_subs", "查看订阅"),
            ("unsubscribe", "取消订阅"),
            ("image", "AI 画图"),
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
    logger.info("Starting DLP Bot...")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # 设置 Bot 初始化 (加载数据库和菜单)
    application.post_init = initialize_data

    # 0. 全局调试记录器 (注册在最前面)
    from telegram.ext import TypeHandler
    application.add_handler(TypeHandler(Update, log_update), group=-1)

    # 1. 独立注册通用按钮 (保证这些按钮永远可点，不受会话状态影响)
    # 处理 help, settings, platforms, back_to_main, ai_chat
    # 注意：排除 download_video, generate_image, back_to_main_cancel 以及 dl_format_ 和 large_file_ 开头的回调
    
    # 1.1 大文件处理按钮
    application.add_handler(CallbackQueryHandler(handle_large_file_action, pattern="^large_file_"))
    
    # 1.2 通用菜单按钮
    common_pattern = "^(?!download_video$|generate_image$|back_to_main_cancel$|dl_format_|large_file_).*$"
    application.add_handler(CallbackQueryHandler(button_callback, pattern=common_pattern))

    # 2. 视频下载对话处理器
    back_handler = CallbackQueryHandler(back_to_main_and_cancel, pattern="^back_to_main_cancel$")
    format_handler = CallbackQueryHandler(handle_download_format, pattern="^dl_format_")
    video_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_download_video, pattern="^download_video$"),
            CommandHandler("download", download_command),
        ],
        states={
            WAITING_FOR_VIDEO_URL: [
                back_handler,
                format_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_video_download),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), back_handler, format_handler],
        allow_reentry=True,
    )
    
    # 3. 画图对话处理器
    image_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_generate_image, pattern="^generate_image$"),
            CommandHandler("image", image_command),
        ],
        states={
            WAITING_FOR_IMAGE_PROMPT: [
                back_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_image_prompt),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), back_handler],
        allow_reentry=True,
    )

    # 4. 注册核心功能处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CommandHandler("translate", toggle_translation_command))
    application.add_handler(CommandHandler("fanyi", toggle_translation_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("monitor", monitor_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("list_subs", list_subs_command))
    application.add_handler(video_conv_handler)
    application.add_handler(image_conv_handler)
    
    # 5. 图片消息处理器（AI 图片分析）
    application.add_handler(
        MessageHandler(filters.PHOTO, handle_ai_photo)
    )
    
    # 6. 视频消息处理器（AI 视频分析）
    application.add_handler(
        MessageHandler(filters.VIDEO, handle_ai_video)
    )
    
    # 7. 语音消息处理器
    application.add_handler(
        MessageHandler(filters.VOICE, handle_voice_message)
    )
    
    # 8. 文档消息处理器（PDF、DOCX）
    application.add_handler(
        MessageHandler(filters.Document.ALL, handle_document)
    )
    
    # 9. AI 对话处理器（兜底文本消息）
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_chat)
    )

    # 启动 Bot
    logger.info("Bot is running...")
    application.run_polling(
        allowed_updates=["message", "callback_query", "edited_message"]
    )


if __name__ == "__main__":
    main()
