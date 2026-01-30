from services.skill_registry_service import skill_registry
from core.skill_loader import skill_loader
from utils import smart_reply_text
from services.skill_creator import update_skill
from core.platform.models import UnifiedContext
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

async def execute(ctx: UnifiedContext, params: dict):
    """
    Execute skill management operations.
    """
    action = params.get("action")
    
    from services.skill_registry_service import skill_registry
    from core.skill_loader import skill_loader
    from utils import smart_reply_text
    
    if action == "search":
        query = params.get("query")
        if not query:
            return "❌ 请提供搜索关键词"
        
        await ctx.reply(f"🔍 正在搜索技能: '{query}'...")
        skills = await skill_registry.search_skills(query)
        
        if not skills:
            return "未找到匹配的技能。你可以尝试其他关键词，或者使用通用能力直接帮助你。"
        
        results = []
        for i, s in enumerate(skills[:5]):
            results.append(f"{i+1}. **{s['name']}** (`{s['repo']}`)\n   {s['description'][:150]}")
        
        response = "找到以下技能：\n\n" + "\n\n".join(results)
        response += "\n\n要安装技能，请说：`安装 <技能名>`"
        return response

    elif action == "install":
        skill_name = params.get("skill_name")
        repo_name = params.get("repo_name")
        
        if not skill_name or not repo_name:
            return "❌ 需要提供 skill_name 和 repo_name"
        
        await ctx.reply(f"⬇️ 正在安装技能: {skill_name}...")
        
        success, message = await skill_registry.install_skill(repo_name, skill_name)
        
        if success:
            # 重新扫描技能
            skill_loader.reload_skills()
            return f"✅ 技能 '{skill_name}' 安装成功！现在可以使用了。"
        else:
            return f"❌ 安装失败: {message}"

    elif action == "delete":
        skill_name = params.get("skill_name")
        if not skill_name:
            return "❌ 请提供要删除的技能名称"
        
        success, message = await skill_registry.delete_skill(skill_name)
        return message

    elif action == "list":
        # 列出所有已安装技能
        index = skill_loader.get_skill_index()
        
        if not index:
            return "当前没有安装任何技能。"
        
        builtin_skills = []
        learned_skills = []
        
        for name, info in index.items():
            skill_type = info.get("skill_type", "unknown")
            source = info.get("source", "unknown")
            desc = info.get("description", "")[:60]
            
            entry = f"• **{name}** ({skill_type}): {desc}"
            
            if source == "builtin":
                builtin_skills.append(entry)
            else:
                learned_skills.append(entry)
        
        response = "📦 **已安装技能**\n\n"
        
        if builtin_skills:
            response += "**内置技能** (不可删除):\n" + "\n".join(builtin_skills) + "\n\n"
        
        if learned_skills:
            response += "**已学习技能** (可删除):\n" + "\n".join(learned_skills)
        else:
            response += "*暂无已学习技能*"
        
        return response

    elif action == "check_updates":
        await ctx.reply("🔄 正在检查技能更新...")
        success, message = await skill_registry.check_updates()
        return message

    elif action == "update":
        await ctx.reply("🔄 正在更新所有技能...")
        success, message = await skill_registry.update_skills()
        
        if success:
            skill_loader.reload_skills()
        
        return message

    elif action == "modify":
        from services.skill_creator import update_skill
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        
        skill_name = params.get("skill_name")
        instruction = params.get("instruction")
        
        if not skill_name or not instruction:
            return "❌ 需要提供 skill_name 和 instruction"
        
        user_id = int(ctx.message.user.id)
        
        await ctx.reply(f"✍️ 正在生成 `{skill_name}` 的修改方案...")
        
        result = await update_skill(skill_name, instruction, user_id)
        
        if not result["success"]:
            return f"❌ 修改失败: {result.get('error', '未知错误')}"
        
        code = result["code"]
        filepath = result["filepath"]
        
        filepath = result["filepath"]
        
        # 存储待审核信息 - Use platform_ctx.user_data
        if hasattr(ctx.platform_ctx, "user_data"):
             ctx.platform_ctx.user_data["pending_skill"] = skill_name
        
        code_preview = code[:500] + "..." if len(code) > 500 else code
        
        keyboard = [
            [
                InlineKeyboardButton("✅ 启用修改", callback_data=f"skill_approve_{skill_name}"),
                InlineKeyboardButton("❌ 放弃", callback_data=f"skill_reject_{skill_name}")
            ],
            [InlineKeyboardButton("📝 查看完整代码", callback_data=f"skill_view_{skill_name}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Using adapter's reply with reply_markup
        await ctx.reply(
            text=(
                f"📝 **Skill 修改草稿**\n\n"
                f"**目标**: `{skill_name}`\n"
                f"**指令**: {instruction}\n\n"
                f"```python\n{code_preview}\n```\n\n"
                f"请确认是否应用修改。"
            ),
            reply_markup=reply_markup
        )
        
        return f"已生成 '{skill_name}' 的修改方案，等待用户审核。"

    elif action == "approve":
        skill_name = params.get("skill_name")
        if not skill_name:
            return "❌ 请提供要批准的技能名称"
        
        from services.skill_creator import approve_skill
        
        result = await approve_skill(skill_name)
        if result["success"]:
            skill_loader.reload_skills()
            return f"✅ 技能 '{skill_name}' 已审核通过并生效！"
        else:
            return f"❌ 审核失败: {result.get('error')}"

    elif action == "reject":
        skill_name = params.get("skill_name")
        if not skill_name:
            return "❌ 请提供要拒绝的技能名称"
        
        from services.skill_creator import reject_skill
        
        result = await reject_skill(skill_name)
        if result["success"]:
            return f"✅ 技能 '{skill_name}' 修改已驳回（删除 pending）。"
        else:
            return f"❌ 驳回失败: {result.get('error')}"

    else:
        return f"❌ 未知操作: {action}。支持的操作: search, install, delete, list, check_updates, update, modify, approve, reject"
