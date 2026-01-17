import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler

from core.config import is_user_allowed

logger = logging.getLogger(__name__)

async def check_permission(update: Update) -> bool:
    """通用权限检查辅助函数"""
    user_id = update.effective_user.id
    if not await is_user_allowed(user_id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ 抱歉，您没有使用此 Bot 的权限。")
        return False
    return True

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """取消当前操作"""
    await update.message.reply_text(
        "操作已取消。\n\n" "发送消息继续 AI 对话，或使用 /download 下载视频。"
    )
    return ConversationHandler.END

# Need to import WELCOME_MESSAGE from somewhere or redefine it. 
# It's better to define constants in a separate file or keep it in start_handlers and import here?
# Actually `back_to_main_and_cancel` uses WELCOME_MESSAGE. 
# To avoid circular imports, maybe put WELCOME_MESSAGE in config.py or a constants.py?
# For now, I will duplicate it or moving it to config is better? 
# Let's put it in constants.py? Or just put it in start_handlers and import it inside the function to avoid top-level circle.
# But `back_to_main_and_cancel` is here.

# Let's see: `back_to_main_and_cancel` constructs the main menu.
# It seems `back_to_main_and_cancel` belongs more in `start_handlers` because it renders the start menu.
# But it is used as a fallback in other handlers.
# I will put `check_permission` and `cancel` here. `back_to_main_and_cancel` will go to `start_handlers.py` to keep menu logic together.
