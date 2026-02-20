"""
Skill 管理 handlers - /teach, /skills 等命令
"""

import logging
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.platform.models import UnifiedContext

from core.config import is_user_admin
from core.skill_loader import skill_loader
from handlers.base_handlers import check_permission_unified, CONVERSATION_END

logger = logging.getLogger(__name__)

# 会话状态
WAITING_FOR_SKILL_DESC = 101


async def teach_command(ctx: UnifiedContext) -> int:
    """
    /teach 命令 - 教 Bot 新能力
    """
    if not await check_permission_unified(ctx):
        return CONVERSATION_END

    if not ctx.platform_ctx:
        return CONVERSATION_END

    args = ctx.platform_ctx.args
    if args:
        # 直接处理
        requirement = " ".join(args)
        return await process_teach(ctx, requirement)

    await ctx.reply(
        "💡 **教我新能力**\n\n"
        "请描述您想让我学会的新功能，例如：\n"
        "• 帮我在豆瓣上签到\n"
        "• 查询天气\n"
        "• 翻译日语\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_SKILL_DESC


async def handle_teach_input(ctx: UnifiedContext) -> int:
    """处理教学输入"""
    text = ctx.message.text
    if not text:
        await ctx.reply("请发送有效描述。")
        return WAITING_FOR_SKILL_DESC

    return await process_teach(ctx, text)


async def process_teach(ctx: UnifiedContext, requirement: str) -> int:
    """处理新能力学习"""
    user_id = ctx.message.user.id

    msg = await ctx.reply("🤔 正在理解您的需求并生成技能...")

    creator = skill_loader.import_skill_module("skill_manager", "creator.py")
    if not creator:
        await ctx.reply("❌ Skill Manager 加载失败")
        return CONVERSATION_END

    result = await creator.create_skill(requirement, user_id)

    if not result["success"]:
        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)),
            f"❌ 生成失败:{result.get('error', '未知错误')}",
        )
        return CONVERSATION_END

    skill_name = result["skill_name"]
    skill_md = result.get("skill_md", "")
    has_scripts = result.get("has_scripts", False)

    skill_name = result["skill_name"]
    skill_md = result.get("skill_md", "")
    has_scripts = result.get("has_scripts", False)

    skill_loader.reload_skills()

    # 显示 SKILL.md 预览
    preview_lines = skill_md.split("\n")[:15]
    preview = "\n".join(preview_lines)
    if len(skill_md.split("\n")) > 15:
        preview += "\n..."

    scripts_info = "\n📦 **包含代码**: 是" if has_scripts else "\n📦 **包含代码**: 否"

    keyboard = [
        [
            InlineKeyboardButton(
                "📝 查看完整内容", callback_data=f"skill_view_{skill_name}"
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await ctx.edit_message(
        getattr(msg, "message_id", getattr(msg, "id", None)),
        f"✅ **新技能已激活**\n\n"
        f"**名称**: `{skill_name}`{scripts_info}\n\n"
        f"```markdown\n{preview}\n```\n\n"
        f"您现在可以直接使用这个技能了。",
        reply_markup=reply_markup,
    )

    return CONVERSATION_END


async def handle_skill_callback(ctx: UnifiedContext) -> None:
    """处理 Skill 相关的回调"""
    data = ctx.callback_data
    if not data:
        return

    await ctx.answer_callback()

    user_id = ctx.message.user.id

    if data.startswith("skill_view_"):
        skill_name = data.replace("skill_view_", "")

        # 查找技能目录或文件 (Directly in learned)
        skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
        learned_dir = os.path.join(skills_base, "learned", skill_name)

        # Remove approve/reject buttons from view mode too, as it's just viewing now
        # Or maybe keep "Delete" button? For now just remove buttons.
        reply_markup = None  # No actions needed for viewing active skill

        # 新格式: 目录结构
        if os.path.isdir(learned_dir):
            skill_md_path = os.path.join(learned_dir, "SKILL.md")
            scripts_dir = os.path.join(learned_dir, "scripts")

            if os.path.exists(skill_md_path):
                try:
                    # 发送 SKILL.md
                    chat_id = ctx.message.chat.id
                    if ctx.platform_ctx:
                        await ctx.platform_ctx.bot.send_document(
                            chat_id=chat_id,
                            document=open(skill_md_path, "rb"),
                            filename="SKILL.md",
                            caption=f"📄 **{skill_name}** - SKILL.md",
                            reply_markup=reply_markup,
                        )

                    # 如果有 scripts,也发送
                    if os.path.isdir(scripts_dir):
                        for script_file in os.listdir(scripts_dir):
                            if script_file.endswith(".py"):
                                script_path = os.path.join(scripts_dir, script_file)
                                if ctx.platform_ctx:
                                    await ctx.platform_ctx.bot.send_document(
                                        chat_id=chat_id,
                                        document=open(script_path, "rb"),
                                        filename=f"scripts/{script_file}",
                                        caption=f"📜 脚本文件: `{script_file}`",
                                    )

                    await ctx.edit_message(
                        ctx.message.id, f"📄 技能文件已发送,请查看上方文档。"
                    )
                except Exception as e:
                    logger.error(f"Failed to send skill files: {e}")
                    await ctx.edit_message(ctx.message.id, f"❌ 发送文件失败:{e}")
            else:
                await ctx.edit_message(ctx.message.id, "❌ SKILL.md 文件不存在")

        else:
            await ctx.edit_message(ctx.message.id, "❌ 技能不存在 (或非目录结构)")


async def skills_command(ctx: UnifiedContext) -> None:
    """
    /skills 命令 - 列出所有可用 Skills
    """
    from core.config import is_user_allowed

    if not await check_permission_unified(ctx):
        return

    index = skill_loader.get_skill_index()

    if not index:
        await ctx.reply("📭 当前没有可用的 Skills")
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

    await ctx.reply("\n".join(msg_parts))


async def reload_skills_command(ctx: UnifiedContext) -> None:
    """
    /reload_skills 命令 - 重新加载所有 Skills（管理员）
    """
    if not is_user_admin(ctx.message.user.id):
        await ctx.reply("❌ 只有管理员可以执行此操作")
        return

    skill_loader.scan_skills()
    skill_loader.reload_skills()

    count = len(skill_loader.get_skill_index())
    await ctx.reply(f"🔄 已重新加载 {count} 个 Skills")
