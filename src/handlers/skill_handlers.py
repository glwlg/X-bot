"""
Skill 管理 handlers - /teach, /skills 等命令
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from core.config import is_user_admin
from core.skill_loader import skill_loader
from core.skill_loader import skill_loader
from services.skill_creator import (
    create_skill, 
    approve_skill, 
    reject_skill, 
    list_pending_skills
)
from handlers.base_handlers import check_permission
from utils import smart_reply_text, smart_edit_text

logger = logging.getLogger(__name__)

# 会话状态
WAITING_FOR_SKILL_DESC = 101


async def teach_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    /teach 命令 - 教 Bot 新能力
    """
    if not await check_permission(update):
        return ConversationHandler.END
    
    args = context.args
    if args:
        # 直接处理
        requirement = " ".join(args)
        return await process_teach(update, context, requirement)
    
    await smart_reply_text(update,
        "💡 **教我新能力**\n\n"
        "请描述您想让我学会的新功能，例如：\n"
        "• 帮我在豆瓣上签到\n"
        "• 查询天气\n"
        "• 翻译日语\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_SKILL_DESC


async def handle_teach_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理教学输入"""
    text = update.message.text
    if not text:
        await smart_reply_text(update, "请发送有效描述。")
        return WAITING_FOR_SKILL_DESC
    
    return await process_teach(update, context, text)


async def process_teach(update: Update, context: ContextTypes.DEFAULT_TYPE, requirement: str) -> int:
    """处理新能力学习"""
    user_id = update.effective_user.id
    
    msg = await smart_reply_text(update, "🤔 正在理解您的需求并生成代码...")
    
    result = await create_skill(requirement, user_id)
    
    if not result["success"]:
        await smart_edit_text(msg, f"❌ 生成失败：{result.get('error', '未知错误')}")
        return ConversationHandler.END
    
    skill_name = result["skill_name"]
    code = result["code"]
    
    # 保存到上下文供后续审核
    context.user_data["pending_skill"] = skill_name
    
    # 显示预览和确认按钮
    code_preview = code[:500] + "..." if len(code) > 500 else code
    
    keyboard = [
        [
            InlineKeyboardButton("✅ 启用", callback_data=f"skill_approve_{skill_name}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"skill_reject_{skill_name}")
        ],
        [InlineKeyboardButton("📝 查看完整代码", callback_data=f"skill_view_{skill_name}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await smart_edit_text(msg,
        f"📝 **新能力草稿**\n\n"
        f"**名称**: `{skill_name}`\n\n"
        f"```python\n{code_preview}\n```\n\n"
        f"确认启用后，您可以通过关键词触发这个能力。",
        reply_markup=reply_markup
    )
    
    return ConversationHandler.END


async def handle_skill_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 Skill 相关的回调"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    if data.startswith("skill_approve_"):
        skill_name = data.replace("skill_approve_", "")
        result = await approve_skill(skill_name)
        
        msg_text = (
            f"✅ 新能力 `{skill_name}` 已启用！\n\n"
            f"现在您可以通过触发词使用它了。"
        ) if result["success"] else f"❌ 启用失败：{result.get('error', '未知错误')}"
        
        # 直接发送新消息，避免编辑文档消息失败
        await smart_reply_text(update, msg_text)
        return
    
    if data.startswith("skill_reject_"):
        skill_name = data.replace("skill_reject_", "")
        result = await reject_skill(skill_name)
        
        msg_text = f"🗑️ 已取消创建 `{skill_name}`" if result["success"] else f"❌ 取消失败：{result.get('error', '未知错误')}"
        
        # 直接发送新消息
        await smart_reply_text(update, msg_text)
        return
    
    if data.startswith("skill_view_"):
        skill_name = data.replace("skill_view_", "")
        # 读取完整代码
        import os
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills", "pending")
        filepath = os.path.join(skills_dir, f"{skill_name}.py")
        
        if os.path.exists(filepath):
            keyboard = [
                [
                    InlineKeyboardButton("✅ 启用", callback_data=f"skill_approve_{skill_name}"),
                    InlineKeyboardButton("❌ 取消", callback_data=f"skill_reject_{skill_name}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # 发送代码文件
            try:
                await query.message.reply_document(
                    document=open(filepath, "rb"),
                    filename=f"{skill_name}.py",
                    caption=f"📄 **{skill_name}.py**\n\n审核后点击下方按钮确认。",
                    reply_markup=reply_markup
                )
                await smart_edit_text(query.message, f"📄 代码已发送为文件，请查看上方文档。")
            except Exception as e:
                logger.error(f"Failed to send code file: {e}")
                await smart_edit_text(query.message, f"❌ 发送文件失败：{e}")
        else:
            await smart_edit_text(query.message, "❌ 代码文件不存在")


async def skills_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /skills 命令 - 列出所有可用 Skills
    """
    if not await check_permission(update):
        return
    
    index = skill_loader.get_skill_index()
    
    if not index:
        await smart_reply_text(update, "📭 当前没有可用的 Skills")
        return
    
    # 分组显示
    builtin = []
    learned = []
    
    for name, info in index.items():
        triggers = ", ".join(info["meta"]["triggers"][:3])
        line = f"• **{name}**: {triggers}"
        
        if info["source"] == "builtin":
            builtin.append(line)
        else:
            learned.append(line)
    
    msg_parts = ["📚 **可用 Skills**\n"]
    
    if builtin:
        msg_parts.append("**内置**:\n" + "\n".join(builtin))
    
    if learned:
        msg_parts.append("\n**已学习**:\n" + "\n".join(learned))
    
    # 待审核
    pending = list_pending_skills()
    if pending and is_user_admin(update.effective_user.id):
        pending_names = [p["name"] for p in pending]
        msg_parts.append(f"\n**待审核** ({len(pending)}):\n• " + "\n• ".join(pending_names))
    
    await smart_reply_text(update, "\n".join(msg_parts))


async def reload_skills_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /reload_skills 命令 - 重新加载所有 Skills（管理员）
    """
    if not is_user_admin(update.effective_user.id):
        await smart_reply_text(update, "❌ 只有管理员可以执行此操作")
        return
    
    skill_loader.scan_skills()
    skill_loader.reload_skills()
    
    count = len(skill_loader.get_skill_index())
    await smart_reply_text(update, f"🔄 已重新加载 {count} 个 Skills")
