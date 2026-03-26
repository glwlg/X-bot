from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import httpx
from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from extension.skills.registry import skill_registry as skill_loader
from ikaros.dev.codex_session_service import codex_session_service

project_root = str(REPO_ROOT)

logger = logging.getLogger(__name__)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    rendered = str(value).strip().lower()
    if rendered in {"1", "true", "yes", "on"}:
        return True
    if rendered in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _to_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return int(default)


def _normalize_backend(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"gemini", "gemini_cli", "gemini-cli"}:
        return "gemini-cli"
    return "codex"


def _resolve_coding_backend(params: dict) -> str:
    if _as_bool(params.get("use_gemini"), default=False):
        return "gemini-cli"
    if _as_bool(params.get("use_codex"), default=False):
        return "codex"

    for key in ("coding_backend", "backend", "provider"):
        if key in params and str(params.get(key) or "").strip():
            return _normalize_backend(params.get(key))

    env_backend = os.getenv("CODING_BACKEND_DEFAULT", "codex")
    return _normalize_backend(env_backend)


def _sanitize_skill_name(value: Any) -> str:
    payload = str(value or "").strip().lower()
    if not payload:
        return ""
    payload = payload.replace("-", "_")
    payload = re.sub(r"[^a-z0-9_]+", "_", payload)
    payload = re.sub(r"_+", "_", payload).strip("_")
    if not payload:
        return ""
    if payload[0].isdigit():
        payload = f"skill_{payload}"
    return payload[:64]


def _default_skill_name() -> str:
    return f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _skill_spec_path() -> str:
    configured = str(
        os.getenv(
            "SKILL_MANAGER_SPEC_PATH",
            "extension/skills/builtin/skill_manager/SKILL_SPEC.md",
        )
        or ""
    ).strip()
    if not configured:
        configured = "extension/skills/builtin/skill_manager/SKILL_SPEC.md"
    if os.path.isabs(configured):
        return configured
    return os.path.abspath(os.path.join(project_root, configured))


def _to_project_rel(path: str) -> str:
    try:
        return os.path.relpath(path, project_root)
    except Exception:
        return path


def _extract_skill_name_hint(text: str) -> str:
    payload = str(text or "")
    matched = re.search(
        r"(?:created_skill|skill_name)\s*[:=]\s*([a-zA-Z0-9_\-]+)",
        payload,
        flags=re.IGNORECASE,
    )
    if not matched:
        return ""
    return str(matched.group(1) or "").strip()


def _extract_backend_from_delivery_result(result: Dict[str, Any], fallback: str) -> str:
    if not isinstance(result, dict):
        return _normalize_backend(fallback)

    data = result.get("data")
    if isinstance(data, dict):
        backend = str(data.get("backend") or data.get("used_backend") or "").strip()
        if backend:
            return _normalize_backend(backend)
        template_result = data.get("template_result")
        if isinstance(template_result, dict):
            nested_backend = str(template_result.get("backend") or "").strip()
            if nested_backend:
                return _normalize_backend(nested_backend)

    backend = str(result.get("backend") or "").strip()
    if backend:
        return _normalize_backend(backend)
    return _normalize_backend(fallback)


async def _run_local_skill_coding_task(
    *,
    _ctx: UnifiedContext,
    _runtime: Any,
    action: str,
    instruction: str,
    cwd: str,
    backend: str,
    skill_name: str = "",
    source: str = "",
) -> Dict[str, Any]:
    _ = (_ctx, _runtime)
    source_label = str(source or f"skill_manager_{action}").strip()
    result = await codex_session_service.start(
        cwd=str(cwd or "").strip(),
        instruction=str(instruction or "").strip(),
        backend=_normalize_backend(backend),
        timeout_sec=_to_int(os.getenv("CODING_BACKEND_TIMEOUT_SEC", "900"), 900),
    )
    data = (
        dict(result.get("data") or {}) if isinstance(result.get("data"), dict) else {}
    )
    session_id = str(data.get("session_id") or "").strip()
    session_status = str(data.get("status") or "").strip().lower()

    if session_status == "waiting_user":
        question = str(
            data.get("question") or result.get("text") or result.get("summary") or ""
        ).strip()
        return {
            "ok": True,
            "waiting_user": True,
            "session_id": session_id,
            "question": question,
            "summary": question or "coding session is waiting for user input",
            "backend": _extract_backend_from_delivery_result(result, backend),
            "source": source_label,
            "tool_result": result,
        }

    if not bool(result.get("ok")) or session_status == "failed":
        message = str(
            result.get("summary")
            or result.get("message")
            or result.get("text")
            or "coding session failed"
        )
        return {
            "ok": False,
            "error": str(result.get("error_code") or "coding_session_failed"),
            "summary": message,
            "backend": _extract_backend_from_delivery_result(result, backend),
            "source": source_label,
            "tool_result": result,
        }

    summary = str(result.get("summary") or "").strip()
    if not summary:
        summary = "coding session completed"

    return {
        "ok": True,
        "summary": summary,
        "backend": _extract_backend_from_delivery_result(result, backend),
        "source": source_label,
        "session_id": session_id,
        "tool_result": result,
    }


async def _create_with_codex_session(
    *,
    ctx: UnifiedContext,
    runtime: Any,
    requirement: str,
    skill_name: str,
    backend: str,
) -> Dict[str, Any]:
    before = {
        name
        for name, info in (skill_loader.get_skill_index() or {}).items()
        if str(info.get("source") or "") != "builtin"
    }

    requested_name = _sanitize_skill_name(skill_name)
    target_name = requested_name or _default_skill_name()
    learned_root = os.path.abspath(os.path.join(skill_loader.skills_dir, "learned"))
    os.makedirs(learned_root, exist_ok=True)
    target_dir = os.path.join(learned_root, target_name)
    os.makedirs(target_dir, exist_ok=True)

    spec_path = _skill_spec_path()
    spec_hint = _to_project_rel(spec_path)
    instruction = (
        "你是 Ikaros 的技能工程师。请在当前工作目录创建一个新技能。\n"
        f"当前工作目录: {target_dir}\n"
        f"目标技能名: {target_name}\n"
        f"开始前先阅读技能规范: {spec_hint}\n"
        f"需求: {requirement}\n"
        "约束:\n"
        "1. 只允许修改当前工作目录及其子目录。\n"
        "2. 必须生成 SKILL.md（YAML frontmatter + 说明文档）。\n"
        "3. 如需代码，创建 scripts/execute.py，函数签名必须是 async def execute(ctx, params, runtime=None)。\n"
        "4. 不要修改 src/ 与其它技能目录。\n"
        "5. 完成后在回复末尾追加一行: CREATED_SKILL=<skill_name>。\n"
    )

    result = await _run_local_skill_coding_task(
        _ctx=ctx,
        _runtime=runtime,
        action="skill_create",
        instruction=instruction,
        cwd=target_dir,
        backend=backend,
        skill_name=target_name,
        source="skill_manager_create",
    )
    if not result.get("ok"):
        return result
    if bool(result.get("waiting_user")):
        return result

    skill_loader.reload_skills()
    after_index = skill_loader.get_skill_index() or {}
    after = {
        name
        for name, info in after_index.items()
        if str(info.get("source") or "") != "builtin"
    }

    resolved_name = ""
    if target_name in after:
        resolved_name = target_name
    if not resolved_name:
        created = sorted(after - before)
        if len(created) == 1:
            resolved_name = created[0]
    if not resolved_name:
        hinted = _extract_skill_name_hint(
            str(result.get("summary") or result.get("tool_result") or "")
        )
        if hinted in after:
            resolved_name = hinted

    if not resolved_name:
        backend_name = str(result.get("backend") or _normalize_backend(backend))
        result["ok"] = False
        result["error"] = "coding_skill_name_unresolved"
        result["summary"] = (
            f"{backend_name} 已执行，但未能定位新技能目录。请在回复中明确 CREATED_SKILL=<name>。"
        )
        return result

    skill_info = skill_loader.get_skill(resolved_name) or {}
    skill_md_path = str(skill_info.get("skill_md_path") or "").strip()
    skill_md = ""
    if skill_md_path and os.path.exists(skill_md_path):
        with open(skill_md_path, "r", encoding="utf-8") as f:
            skill_md = f.read()

    result["resolved_skill_name"] = resolved_name
    result["target_skill_name"] = target_name
    result["skill_md"] = skill_md
    return result


async def _modify_with_codex_session(
    *,
    ctx: UnifiedContext,
    runtime: Any,
    skill_name: str,
    instruction: str,
    backend: str,
) -> Dict[str, Any]:
    skill_info = skill_loader.get_skill(skill_name)
    if not skill_info:
        return {
            "ok": False,
            "error": f"skill_not_found:{skill_name}",
            "summary": f"Skill '{skill_name}' 不存在",
        }
    if str(skill_info.get("source") or "") == "builtin":
        return {
            "ok": False,
            "error": "builtin_skill_readonly",
            "summary": "系统技能受保护，无法修改。",
        }

    skill_dir = str(skill_info.get("skill_dir") or "").strip()
    spec_path = _skill_spec_path()
    spec_hint = _to_project_rel(spec_path)
    cli_instruction = (
        "你是 Ikaros 的技能维护工程师，请修改一个已有技能。\n"
        f"目标技能: {skill_name}\n"
        f"当前工作目录: {skill_dir}\n"
        f"开始前先阅读技能规范: {spec_hint}\n"
        f"需求: {instruction}\n"
        "限制:\n"
        "1. 只修改当前工作目录及其子目录（SKILL.md / scripts/*.py）。\n"
        "2. 不要改动 src/ 与其它技能目录。\n"
        "3. 保持技能可加载（SKILL.md frontmatter 完整）。\n"
    )
    result = await _run_local_skill_coding_task(
        _ctx=ctx,
        _runtime=runtime,
        action="skill_modify",
        instruction=cli_instruction,
        cwd=skill_dir,
        backend=backend,
        skill_name=skill_name,
        source="skill_manager_modify",
    )
    if bool(result.get("waiting_user")):
        return result
    if result.get("ok"):
        skill_loader.reload_skills()
    return result


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> Dict[str, Any]:
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
            best_tool = f"ext_{best_skill.replace('-', '_')}"
            response += (
                f"\n\n[SYSTEM HINT] Found high confidence match: '{best_skill}'. "
                f"You should now invoke tool `{best_tool}` to fulfill the user's request."
            )

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
            return {"text": "🔇🔇🔇❌ 请提供要安装的技能名称或 URL", "ui": {}}

        # User ID needed for adoption ownership
        user_id = int(ctx.message.user.id) if ctx.message.user else 0

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

        backend = _resolve_coding_backend(params)
        cli_result = await _modify_with_codex_session(
            ctx=ctx,
            runtime=runtime,
            skill_name=str(skill_name),
            instruction=str(instruction),
            backend=backend,
        )
        if cli_result.get("waiting_user"):
            session_id = str(cli_result.get("session_id") or "").strip()
            question = str(
                cli_result.get("question") or cli_result.get("summary") or ""
            ).strip()
            return {
                "text": (
                    f"🔇🔇🔇⏸ Skill `{skill_name}` 修改需要进一步确认（session_id=`{session_id}`）。\n\n"
                    f"{question}\n\n"
                    "请直接继续回答这个问题，我会用 `codex_session` 接着完成技能修改。"
                ),
                "ui": {},
                "session_id": session_id,
            }
        if cli_result.get("ok"):
            used_backend = str(cli_result.get("backend") or backend)
            return {
                "text": (
                    f"🔇🔇🔇✅ Skill '{skill_name}' 已通过 `codex_session` "
                    f"（backend=`{used_backend}`）修改并生效。"
                ),
                "ui": {},
            }

        summary = str(
            cli_result.get("summary") or cli_result.get("error") or "未知错误"
        )
        return {
            "text": (
                f"🔇🔇🔇❌ ikaros 调用 `codex_session` 技能修改流程失败 "
                f"(backend=`{backend}`): {summary}"
            ),
            "ui": {},
        }

    elif action == "approve":
        return {"text": "🔇🔇🔇⚠️ 技能创建现已自动生效，不再需要手动批准。", "ui": {}}

    elif action == "reject":
        return {
            "text": "🔇🔇🔇⚠️ 技能创建流程已变更 (无草稿阶段)。如需删除技能，请使用 `delete skill <name>`。",
            "ui": {},
        }

    elif action == "create":
        requirement = params.get("requirement") or params.get("instruction")
        if not requirement:
            return {"text": "🔇🔇🔇❌ 请提供技能需求描述 (requirement)", "ui": {}}

        backend = _resolve_coding_backend(params)
        cli_result = await _create_with_codex_session(
            ctx=ctx,
            runtime=runtime,
            requirement=str(requirement),
            skill_name=str(params.get("skill_name") or ""),
            backend=backend,
        )
        if cli_result.get("waiting_user"):
            session_id = str(cli_result.get("session_id") or "").strip()
            question = str(
                cli_result.get("question") or cli_result.get("summary") or ""
            ).strip()
            return {
                "text": (
                    f"🔇🔇🔇⏸ Skill 创建需要进一步确认（session_id=`{session_id}`）。\n\n"
                    f"{question}\n\n"
                    "请直接继续回答这个问题，我会用 `codex_session` 接着完成技能创建。"
                ),
                "ui": {},
                "session_id": session_id,
            }
        if cli_result.get("ok"):
            resolved_name = str(cli_result.get("resolved_skill_name") or "").strip()
            used_backend = str(cli_result.get("backend") or backend)
            if resolved_name:
                skill_loader.reload_skills()
                skill_info = skill_loader.get_skill(resolved_name) or {}
                has_scripts = bool(skill_info.get("scripts"))
                return {
                    "text": (
                        f"🔇🔇🔇✅ 技能 `{resolved_name}` 已通过 `codex_session` "
                        f"（backend=`{used_backend}`）创建并生效。"
                    ),
                    "ui": {},
                    "created_skill_name": resolved_name,
                    "used_backend": used_backend,
                    "skill_md": str(cli_result.get("skill_md") or ""),
                    "has_scripts": has_scripts,
                }
            return {
                "text": (
                    f"🔇🔇🔇✅ ikaros 通过 `codex_session` 技能创建流程 "
                    f"(backend=`{used_backend}`) 完成技能创建，但未识别到技能名。"
                    "请执行 `list skills` 确认。"
                ),
                "ui": {},
                "used_backend": used_backend,
            }

        summary = str(
            cli_result.get("summary") or cli_result.get("error") or "未知错误"
        )
        return {
            "text": (
                f"🔇🔇🔇❌ ikaros 调用 `codex_session` 技能创建流程失败 "
                f"(backend=`{backend}`): {summary}"
            ),
            "ui": {},
        }

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
            result = await adopt_skill(content, user_id)

            if result["success"]:
                # Skill is directly adopted and active
                return True, f"技能 '{result['skill_name']}' 已成功安装！"
            else:
                return False, f"安装失败 (解析阶段): {result.get('error')}"

    except Exception as e:
        logger.error(f"Install skill error: {e}")
        return False, str(e)


async def adopt_skill(content: str, user_id: int) -> dict:
    """
    Adopt an existing skill content (install from URL) directly into learned.
    Only supports standard SKILL.md.
    """
    try:
        skill_name = ""

        # 1. Detect Type & Extract Name
        if content.startswith("---"):
            # Parse YAML frontmatter
            import yaml

            try:
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1])
                    skill_name = frontmatter.get("name")
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to parse SKILL.md frontmatter: {e}",
                }
        else:
            return {
                "success": False,
                "error": "Invalid skill format. Must start with '---' (SKILL.md). Legacy format is not supported.",
            }

        if not skill_name:
            return {
                "success": False,
                "error": "Could not extract 'name' from skill content.",
            }

        # 2. Save directly to Learned
        skills_base = skill_loader.skills_dir
        skill_dir = os.path.join(skills_base, "learned", skill_name)
        os.makedirs(skill_dir, exist_ok=True)

        # Save SKILL.md
        md_path = os.path.join(skill_dir, "SKILL.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Fix permissions
        try:
            builtin_dir = os.path.join(skills_base, "builtin")
            if os.path.exists(builtin_dir):
                st = os.stat(builtin_dir)
                target_uid = st.st_uid
                target_gid = st.st_gid

                for root, dirs, files in os.walk(skill_dir):
                    os.chown(root, target_uid, target_gid)
                    for d in dirs:
                        os.chown(os.path.join(root, d), target_uid, target_gid)
                    for f in files:
                        os.chown(os.path.join(root, f), target_uid, target_gid)
        except Exception:
            pass

        filepath = md_path

        logger.info(f"Adopted skill (Direct): {skill_name} -> {filepath}")

        return {"success": True, "skill_name": skill_name, "path": filepath}

    except Exception as e:
        logger.error(f"Adopt skill error: {e}")
        return {"success": False, "error": str(e)}


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Skill manager CLI bridge.",
    )
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List installed skills")

    search_parser = subparsers.add_parser("search", help="Search local skills")
    search_parser.add_argument("query", help="Search query")

    install_parser = subparsers.add_parser("install", help="Install a skill")
    install_parser.add_argument("target", help="Skill URL or GitHub owner/repo")

    delete_parser = subparsers.add_parser("delete", help="Delete a learned skill")
    delete_parser.add_argument("skill_name", help="Skill name")

    create_parser = subparsers.add_parser("create", help="Create a new skill")
    create_parser.add_argument("requirement", help="Skill requirement")
    create_parser.add_argument("--skill-name", default="", help="Preferred skill name")
    create_parser.add_argument("--backend", default="", help="codex or gemini-cli")

    modify_parser = subparsers.add_parser("modify", help="Modify an existing skill")
    modify_parser.add_argument("skill_name", help="Skill name")
    modify_parser.add_argument("instruction", help="Modification instruction")
    modify_parser.add_argument("--backend", default="", help="codex or gemini-cli")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "").strip().lower()
    if command == "list":
        return merge_params(args, {"action": "list"})
    if command == "search":
        return merge_params(
            args,
            {"action": "search", "query": str(args.query or "").strip()},
        )
    if command == "install":
        target = str(args.target or "").strip()
        return merge_params(
            args,
            {
                "action": "install",
                "url": target,
                "skill_name": target,
                "repo_name": target,
            },
        )
    if command == "delete":
        return merge_params(
            args,
            {"action": "delete", "skill_name": str(args.skill_name or "").strip()},
        )
    if command == "create":
        explicit: dict[str, Any] = {
            "action": "create",
            "requirement": str(args.requirement or "").strip(),
            "instruction": str(args.requirement or "").strip(),
            "skill_name": str(args.skill_name or "").strip(),
        }
        if str(args.backend or "").strip():
            explicit["backend"] = str(args.backend).strip()
        return merge_params(args, explicit)
    if command == "modify":
        explicit = {
            "action": "modify",
            "skill_name": str(args.skill_name or "").strip(),
            "instruction": str(args.instruction or "").strip(),
        }
        if str(args.backend or "").strip():
            explicit["backend"] = str(args.backend).strip()
        return merge_params(args, explicit)
    raise SystemExit(f"unsupported command: {command}")


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
