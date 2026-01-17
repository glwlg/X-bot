import logging
from telegram import Update
from telegram.ext import ContextTypes

from core.config import is_user_admin
from repositories import add_allowed_user, remove_allowed_user
from .base_handlers import check_permission

logger = logging.getLogger(__name__)

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """添加用户到白名单 (仅限管理员)"""
    user_id = update.effective_user.id
    message = update.effective_message
    
    if not is_user_admin(user_id):
        if message: await message.reply_text("⛔ 您不是管理员，无法执行此操作。")
        return

    try:
        # 获取参数 /adduser 123456 [备注]
        args = context.args
        if not args:
            if message: await message.reply_text("用法: /adduser <user_id> [备注]")
            return
            
        target_id = int(args[0])
        description = " ".join(args[1:]) if len(args) > 1 else "Added via command"
        
        await add_allowed_user(target_id, added_by=user_id, description=description)
        if message: await message.reply_text(f"✅ 用户 {target_id} 已添加到白名单。")
        
    except ValueError:
        if message: await message.reply_text("❌ 用户 ID 必须是数字。")
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            if message: await message.reply_text(f"⚠️ 用户 {target_id} 已经在白名单中了。")
        else:
            logger.error(f"Error adding user: {e}")
            if message: await message.reply_text("❌ 添加失败，请检查日志。")


async def deluser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """从白名单移除用户 (仅限管理员)"""
    user_id = update.effective_user.id
    message = update.effective_message

    if not is_user_admin(user_id):
        if message: await message.reply_text("⛔ 您不是管理员，无法执行此操作。")
        return

    try:
        args = context.args
        if not args:
            if message: await message.reply_text("用法: /deluser <user_id>")
            return
            
        target_id = int(args[0])
        
        await remove_allowed_user(target_id)
        if message: await message.reply_text(f"✅ 用户 {target_id} 已从白名单移除。")
        
    except ValueError:
        if message: await message.reply_text("❌ 用户 ID 必须是数字。")
    except Exception as e:
        logger.error(f"Error removing user: {e}")
        if message: await message.reply_text("❌ 移除失败，请检查日志。")
