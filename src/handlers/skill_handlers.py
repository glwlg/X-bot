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
    
    msg = await smart_reply_text(update, "🤔 正在理解您的需求并生成技能...")
    
    result = await create_skill(requirement, user_id)
    
    if not result["success"]:
        await smart_edit_text(msg, f"❌ 生成失败:{result.get('error', '未知错误')}")
        return ConversationHandler.END
    
    skill_name = result["skill_name"]
    skill_md = result.get("skill_md", "")
    has_scripts = result.get("has_scripts", False)
    
    # 保存到上下文供后续审核
    context.user_data["pending_skill"] = skill_name
    
    # 显示 SKILL.md 预览
    preview_lines = skill_md.split("\n")[:15]
    preview = "\n".join(preview_lines)
    if len(skill_md.split("\n")) > 15:
        preview += "\n..."
    
    scripts_info = "\n📦 **包含代码**: 是" if has_scripts else "\n📦 **包含代码**: 否"
    
    keyboard = [
        [
            InlineKeyboardButton("✅ 启用", callback_data=f"skill_approve_{skill_name}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"skill_reject_{skill_name}")
        ],
        [InlineKeyboardButton("📝 查看完整内容", callback_data=f"skill_view_{skill_name}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await smart_edit_text(msg,
        f"📝 **新技能草稿**\n\n"
        f"**名称**: `{skill_name}`{scripts_info}\n\n"
        f"```markdown\n{preview}\n```\n\n"
        f"确认启用后,您可以使用这个技能。",
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
        
        # 查找技能目录或文件
        import os
        skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
        pending_dir = os.path.join(skills_base, "pending", skill_name)
        pending_file = os.path.join(skills_base, "pending", f"{skill_name}.py")
        
        keyboard = [
            [
                InlineKeyboardButton("✅ 启用", callback_data=f"skill_approve_{skill_name}"),
                InlineKeyboardButton("❌ 取消", callback_data=f"skill_reject_{skill_name}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # 新格式: 目录结构
        if os.path.isdir(pending_dir):
            skill_md_path = os.path.join(pending_dir, "SKILL.md")
            scripts_dir = os.path.join(pending_dir, "scripts")
            
            if os.path.exists(skill_md_path):
                try:
                    # 发送 SKILL.md
                    await query.message.reply_document(
                        document=open(skill_md_path, "rb"),
                        filename="SKILL.md",
                        caption=f"📄 **{skill_name}** - SKILL.md\n\n审核后点击下方按钮确认。",
                        reply_markup=reply_markup
                    )
                    
                    # 如果有 scripts,也发送
                    if os.path.isdir(scripts_dir):
                        for script_file in os.listdir(scripts_dir):
                            if script_file.endswith(".py"):
                                script_path = os.path.join(scripts_dir, script_file)
                                await query.message.reply_document(
                                    document=open(script_path, "rb"),
                                    filename=f"scripts/{script_file}",
                                    caption=f"📜 脚本文件: `{script_file}`"
                                )
                    
                    await smart_edit_text(query.message, f"📄 技能文件已发送,请查看上方文档。")
                except Exception as e:
                    logger.error(f"Failed to send skill files: {e}")
                    await smart_edit_text(query.message, f"❌ 发送文件失败:{e}")
            else:
                await smart_edit_text(query.message, "❌ SKILL.md 文件不存在")
        
        # 旧格式: 单个 .py 文件
        elif os.path.exists(pending_file):
            try:
                await query.message.reply_document(
                    document=open(pending_file, "rb"),
                    filename=f"{skill_name}.py",
                    caption=f"📄 **{skill_name}.py**\n\n审核后点击下方按钮确认。",
                    reply_markup=reply_markup
                )
                await smart_edit_text(query.message, f"📄 代码已发送为文件,请查看上方文档。")
            except Exception as e:
                logger.error(f"Failed to send code file: {e}")
                await smart_edit_text(query.message, f"❌ 发送文件失败:{e}")
        else:
            await smart_edit_text(query.message, "❌ 技能文件不存在")


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
        description = info.get("description", "")[:60]
        # 标准格式没有 triggers,显示描述
        line = f"• **{name}**: {description}"
        
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
