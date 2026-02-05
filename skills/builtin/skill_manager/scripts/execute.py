import logging
import os
import shutil
import sys
import httpx
from typing import Tuple, Dict, Any

from core.platform.models import UnifiedContext
from core.skill_loader import skill_loader

# Ensure we can import local modules (creator.py)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

import creator  # local import

logger = logging.getLogger(__name__)


async def execute(ctx: UnifiedContext, params: dict) -> Dict[str, Any]:
    """
    Execute skill management operations.
    """
    action = params.get("action")

    if action == "search":
        query = params.get("query")
        if not query:
            return {"text": "🔇🔇🔇❌ 请提供搜索关键词", "ui": {}}

        # 1. Search Local Index
        logger.info(f"[SkillManager] Local search query: '{query}'")
        logger.info("============================================")
        logger.info("============================================")
        logger.info("============================================")
        local_matches = await skill_loader.find_similar_skills(query)
        logger.info(
            f"[SkillManager] Local search query: '{query}', Matches: {len(local_matches)}"
        )
        for m in local_matches:
            logger.info(f" - Found: {m['name']} (score: {m.get('score')})")

        if not local_matches:
            return {"text": "🔇🔇🔇未找到匹配的技能。", "ui": {}}

        response_parts = []

        if local_matches:
            lines = ["📦 **本地已安装技能**"]
            for s in local_matches[:3]:
                score_str = (
                    f" (匹配度: {s.get('score', 0):.2f})" if s.get("score") else ""
                )
                lines.append(f"• **{s['name']}**{score_str}: {s['description'][:100]}")
            response_parts.append("\n".join(lines))

        response = "\n\n".join(response_parts)

        # Add explicit instruction for Agent to use the best match
        if local_matches:
            best_skill = local_matches[0]["name"]
            response += f"\n\n[SYSTEM HINT] Found high confidence match: '{best_skill}'. You should now call `call_skill(skill_name='{best_skill}', ...)` to fulfill the user's request."

        response += "\n\n要安装技能，请说：`安装 <技能名>` 或 `安装 <GitHub 链接>`"

        # Return structured
        return {"text": "🔇🔇🔇" + response, "ui": {}}

    elif action == "install":
        skill_name = params.get("skill_name")
        repo_name = params.get("repo_name")
        url = params.get("url")

        # Support single argument "install <URL>" mapped to skill_name or repo_name
        # Also support explicit "url" param
        target = url or skill_name or repo_name

        if not target:
            return "❌ 请提供要安装的技能名称或 URL"

        # User ID needed for adoption ownership
        user_id = ctx.message.user.id if ctx.message.user else "0"

        success, message = await _install_skill(target, user_id)

        if success:
            # 重新扫描技能
            skill_loader.reload_skills()
            # skill_loader.reload_skills()
            return {"text": "🔇🔇🔇" + message, "ui": {}}
        else:
            return {"text": f"🔇🔇🔇❌ 安装失败: {message}", "ui": {}}

    elif action == "delete":
        skill_name = params.get("skill_name")
        if not skill_name:
            return {"text": "🔇🔇🔇❌ 请提供要删除的技能名称", "ui": {}}

        success, message = _delete_skill(skill_name)
        return {"text": "🔇🔇🔇" + message, "ui": {}}

    elif action == "list":
        # 列出所有已安装技能
        index = skill_loader.get_skill_index()

        if not index:
            return {"text": "🔇🔇🔇当前没有安装任何技能。", "ui": {}}

        builtin_skills = []
        learned_skills = []

        for name, info in index.items():
            source = info.get("source", "unknown")
            desc = info.get("description", "")[:60]

            entry = f"• **{name}**: {desc}"

            if source == "builtin":
                builtin_skills.append(entry)
            else:
                learned_skills.append(entry)

        response = "📦 **已安装技能**\n\n"

        if builtin_skills:
            response += (
                "**内置技能** (不可删除):\n" + "\n".join(builtin_skills) + "\n\n"
            )

        if learned_skills:
            response += "**已学习技能** (可删除):\n" + "\n".join(learned_skills)
        else:
            response += "*暂无已学习技能*"

        return {"text": "🔇🔇🔇" + response, "ui": {}}

    elif action == "check_updates":
        # Deprecated
        return {
            "text": "🔇🔇🔇⚠️ 技能更新现已由 AI 自动管理。您可以使用 'modify skill' 或自然语言让 Bot 更新技能。",
            "ui": {},
        }

    elif action == "update":
        # Deprecated
        return {"text": "🔇🔇🔇⚠️ 技能更新现已由 AI 自动管理。", "ui": {}}

    elif action == "modify":
        skill_name = params.get("skill_name")
        instruction = params.get("instruction")

        if not skill_name or not instruction:
            return {"text": "🔇🔇🔇❌ 需要提供 skill_name 和 instruction", "ui": {}}

        user_id = ctx.message.user.id

        # Use update_skill (AI Refactoring)
        result = await creator.update_skill(skill_name, instruction, user_id)

        if not result["success"]:
            return {
                "text": f"🔇🔇🔇❌ 修改失败: {result.get('error', '未知错误')}",
                "ui": {},
            }

        # 重新加载技能
        skill_loader.reload_skills()

        return {"text": f"🔇🔇🔇✅ Skill '{skill_name}' 修改成功并已生效！", "ui": {}}

    elif action == "approve":
        return {"text": "🔇🔇🔇⚠️ 技能创建现已自动生效，不再需要手动批准。", "ui": {}}

    elif action == "reject":
        return {
            "text": "🔇🔇🔇⚠️ 技能创建流程已变更 (无草稿阶段)。如需删除技能，请使用 `delete skill <name>`。",
            "ui": {},
        }

    elif action == "create":
        # New capability: Create Skill via Evolution Router (Smart)
        requirement = params.get("requirement") or params.get("instruction")
        if not requirement:
            return {"text": "🔇🔇🔇❌ 请提供技能需求描述 (requirement)", "ui": {}}

        user_id = ctx.message.user.id

        # Use Evolution Router to decide Strategy (Create vs Reuse vs Config)
        from core.evolution_router import evolution_router

        # result_msg = await evolution_router.evolve(requirement, user_id, ctx)
        # ctx passed might trigger log error but no reply now
        result_msg = await evolution_router.evolve(requirement, user_id, ctx)

        return {"text": "🔇🔇🔇" + result_msg, "ui": {}}

    else:
        return {
            "text": f"🔇🔇🔇❌ 未知操作: {action}。支持的操作: search, install, create, delete, list, modify, approve, reject, config, tasks, delete_task",
            "ui": {},
        }


# --- Helper Functions ---


async def _install_skill(target: str, user_id: int) -> Tuple[bool, str]:
    """Install/Adopt skill from URL or Repo"""
    try:
        target_url = ""

        # 1. Check if repo is actually a URL
        if target.startswith("http://") or target.startswith("https://"):
            target_url = target

        # 2. If it's a repo string (user/repo), try to find SKILL.md
        elif "/" in target:
            target_url = f"https://raw.githubusercontent.com/{target}/main/SKILL.md"

        if not target_url:
            return False, "请提供有效的 Skill URL 或 GitHub 仓库地址 (格式: user/repo)"

        logger.info(f"Installing skill from URL: {target_url}")

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(target_url)

            # Check main branch first, if 404 try master
            if response.status_code == 404 and "main" in target_url:
                target_url = target_url.replace("main", "master")
                response = await client.get(target_url)

            if response.status_code != 200:
                return False, f"无法下载技能文件: {response.status_code} ({target_url})"

            content = response.text

            # Verify content
            if "SKILL_META" not in content and not content.startswith("---"):
                return (
                    False,
                    "目标文件看起来不像是一个有效的 Skill (未找到 SKILL_META 或 YAML frontmatter)",
                )

            # Adopt
            result = await creator.adopt_skill(content, user_id)

            if result["success"]:
                # Skill is directly adopted and active
                return True, f"技能 '{result['skill_name']}' 已成功安装！"
            else:
                return False, f"安装失败 (解析阶段): {result.get('error')}"

    except Exception as e:
        logger.error(f"Install skill error: {e}")
        return False, str(e)


def _delete_skill(skill_name: str) -> Tuple[bool, str]:
    """Delete a learned skill"""
    try:
        skill_info = skill_loader.get_skill(skill_name)

        if not skill_info:
            return False, f"❌ 技能 '{skill_name}' 不存在"

        if skill_info.get("source") == "builtin":
            return False, f"🚫 禁止删除内置技能 '{skill_name}'"

        skill_path = skill_info.get("skill_dir")

        if not skill_path or not os.path.exists(skill_path):
            return False, f"❌ 找不到技能文件: {skill_path}"

        # Security check: MUST be in learned dir
        learned_dir_abs = os.path.abspath(
            os.path.join(skill_loader.skills_dir, "learned")
        )
        skill_path_abs = os.path.abspath(skill_path)

        if not skill_path_abs.startswith(learned_dir_abs):
            return False, "🚫 安全限制：只能删除 learned 目录下的技能"

        if os.path.isdir(skill_path_abs):
            shutil.rmtree(skill_path_abs)
        else:
            os.remove(skill_path_abs)

        skill_loader.unload_skill(skill_name)
        skill_loader.reload_skills()

        return True, f"✅ 已删除技能 '{skill_name}'"

    except Exception as e:
        return False, f"删除异常: {e}"
