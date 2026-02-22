import asyncio
import contextlib
import logging
import os
import re
import shlex
import shutil
import sys
import httpx
from datetime import datetime
from typing import Tuple, Dict, Any

from core.platform.models import UnifiedContext
from core.skill_loader import skill_loader

# Ensure we can import local modules (creator.py)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
project_root = os.path.abspath(
    os.path.join(current_dir, "..", "..", "..", "..")
)

import creator  # local import

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

    env_backend = os.getenv("SKILL_MANAGER_CODING_BACKEND", "codex")
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
            "skills/builtin/skill_manager/SKILL_SPEC.md",
        )
        or ""
    ).strip()
    if not configured:
        configured = "skills/builtin/skill_manager/SKILL_SPEC.md"
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


def _tail(text: str, max_chars: int = 1200) -> str:
    payload = str(text or "").strip()
    if len(payload) <= max_chars:
        return payload
    return payload[-max_chars:]


def _is_codex_repo_trust_error(text: str) -> bool:
    payload = str(text or "").lower()
    return (
        "not inside a trusted directory" in payload
        and "--skip-git-repo-check" in payload
    )


def _inject_skip_git_repo_check(args: list[str]) -> list[str]:
    if "--skip-git-repo-check" in args:
        return list(args)
    patched = list(args)
    if "exec" in patched:
        idx = patched.index("exec")
        patched.insert(idx + 1, "--skip-git-repo-check")
        return patched
    patched.append("--skip-git-repo-check")
    return patched


def _build_coding_cli_command(backend: str, instruction: str) -> tuple[str, list[str]]:
    safe_instruction = str(instruction or "").strip()
    backend_name = _normalize_backend(backend)
    if backend_name == "gemini-cli":
        cmd = (
            os.getenv("SKILL_MANAGER_GEMINI_COMMAND")
            or "gemini-cli"
        ).strip()
        template = (
            os.getenv("SKILL_MANAGER_GEMINI_ARGS_TEMPLATE")
            or "--prompt {instruction}"
        ).strip()
    else:
        cmd = (
            os.getenv("SKILL_MANAGER_CODEX_COMMAND")
            or "codex"
        ).strip()
        template = (
            os.getenv("SKILL_MANAGER_CODEX_ARGS_TEMPLATE")
            or "exec {instruction}"
        ).strip()

    rendered = template.format(instruction=shlex.quote(safe_instruction))
    args = shlex.split(rendered)
    return cmd, args


async def _run_manager_coding_cli_task(
    *,
    backend: str,
    instruction: str,
    source: str,
    cwd: str = "",
) -> Dict[str, Any]:
    backend_name = _normalize_backend(backend)
    timeout_sec = max(30, int(os.getenv("SKILL_MANAGER_CLI_TIMEOUT_SEC", "900")))
    cmd, initial_args = _build_coding_cli_command(backend_name, instruction)
    run_cwd = str(cwd or os.getenv("SKILL_MANAGER_REPO_ROOT", project_root) or "").strip()
    if not run_cwd:
        run_cwd = project_root
    auto_skip_repo_check = _as_bool(
        os.getenv("SKILL_MANAGER_CODEX_AUTO_SKIP_GIT_REPO_CHECK", "true"),
        default=True,
    )

    async def _run_once(run_args: list[str]) -> Dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd,
                *run_args,
                cwd=run_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "cli_not_found",
                "backend": backend_name,
                "summary": f"{backend_name} command not found: {cmd}",
                "source": source,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": "exec_prepare_failed",
                "backend": backend_name,
                "summary": str(exc),
                "source": source,
            }

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            stdout_raw, stderr_raw = await proc.communicate()
            stdout = stdout_raw.decode("utf-8", errors="ignore").strip()
            stderr = stderr_raw.decode("utf-8", errors="ignore").strip()
            return {
                "ok": False,
                "error": "cli_timeout",
                "backend": backend_name,
                "summary": f"{backend_name} timed out after {timeout_sec}s",
                "stdout": _tail(stdout),
                "stderr": _tail(stderr),
                "source": source,
            }

        stdout = stdout_raw.decode("utf-8", errors="ignore").strip()
        stderr = stderr_raw.decode("utf-8", errors="ignore").strip()
        combined = "\n".join(item for item in (stdout, stderr) if item).strip()
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "cli_exec_failed",
                "backend": backend_name,
                "exit_code": int(proc.returncode or 0),
                "summary": _tail(
                    combined or f"{backend_name} exited with non-zero status"
                ),
                "stdout": _tail(stdout),
                "stderr": _tail(stderr),
                "source": source,
            }

        return {
            "ok": True,
            "backend": backend_name,
            "summary": _tail(combined or f"{backend_name} task completed"),
            "stdout": _tail(stdout, max_chars=4000),
            "stderr": _tail(stderr, max_chars=4000),
            "source": source,
        }

    first = await _run_once(initial_args)
    if first.get("ok"):
        return first

    if backend_name != "codex" or not auto_skip_repo_check:
        return first

    if str(first.get("error") or "") != "cli_exec_failed":
        return first

    first_output = "\n".join(
        [
            str(first.get("summary") or ""),
            str(first.get("stderr") or ""),
            str(first.get("stdout") or ""),
        ]
    )
    if not _is_codex_repo_trust_error(first_output):
        return first

    retry_args = _inject_skip_git_repo_check(initial_args)
    logger.warning(
        "[SkillManager] codex trust check hit, retrying with --skip-git-repo-check"
    )
    second = await _run_once(retry_args)
    if second.get("ok"):
        second["retry_hint"] = "skip_git_repo_check"
        return second
    return second


async def _create_with_manager_cli(
    *,
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
        "你是 X-Bot 的技能工程师。请在当前工作目录创建一个新技能。\n"
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

    result = await _run_manager_coding_cli_task(
        backend=backend,
        instruction=instruction,
        source="skill_manager_create",
        cwd=target_dir,
    )
    if not result.get("ok"):
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
            str(
                result.get("summary")
                or result.get("stdout")
                or result.get("stderr")
                or ""
            )
        )
        if hinted in after:
            resolved_name = hinted

    if not resolved_name:
        backend_name = str(result.get("backend") or _normalize_backend(backend))
        result["ok"] = False
        result["error"] = "cli_create_skill_name_unresolved"
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


async def _modify_with_manager_cli(
    *,
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
        "你是 X-Bot 的技能维护工程师，请修改一个已有技能。\n"
        f"目标技能: {skill_name}\n"
        f"当前工作目录: {skill_dir}\n"
        f"开始前先阅读技能规范: {spec_hint}\n"
        f"需求: {instruction}\n"
        "限制:\n"
        "1. 只修改当前工作目录及其子目录（SKILL.md / scripts/*.py）。\n"
        "2. 不要改动 src/ 与其它技能目录。\n"
        "3. 保持技能可加载（SKILL.md frontmatter 完整）。\n"
    )
    result = await _run_manager_coding_cli_task(
        backend=backend,
        instruction=cli_instruction,
        source="skill_manager_modify",
        cwd=skill_dir,
    )
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

        backend = _resolve_coding_backend(params)
        cli_result = await _modify_with_manager_cli(
            skill_name=str(skill_name),
            instruction=str(instruction),
            backend=backend,
        )
        if cli_result.get("ok"):
            used_backend = str(cli_result.get("backend") or backend)
            return {
                "text": f"🔇🔇🔇✅ Skill '{skill_name}' 已由 manager 使用 `{used_backend}` 修改并生效。",
                "ui": {},
            }

        summary = str(cli_result.get("summary") or cli_result.get("error") or "未知错误")
        return {
            "text": f"🔇🔇🔇❌ manager 调用 `{backend}` 修改失败: {summary}",
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
        cli_result = await _create_with_manager_cli(
            requirement=str(requirement),
            skill_name=str(params.get("skill_name") or ""),
            backend=backend,
        )
        if cli_result.get("ok"):
            resolved_name = str(cli_result.get("resolved_skill_name") or "").strip()
            used_backend = str(cli_result.get("backend") or backend)
            if resolved_name:
                skill_loader.reload_skills()
                return {
                    "text": f"🔇🔇🔇✅ 技能 `{resolved_name}` 已由 manager 使用 `{used_backend}` 创建并生效。",
                    "ui": {},
                }
            return {
                "text": f"🔇🔇🔇✅ manager 使用 `{used_backend}` 完成技能创建，但未识别到技能名。请执行 `list skills` 确认。",
                "ui": {},
            }

        summary = str(cli_result.get("summary") or cli_result.get("error") or "未知错误")
        return {
            "text": f"🔇🔇🔇❌ manager 调用 `{backend}` 创建技能失败: {summary}",
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
