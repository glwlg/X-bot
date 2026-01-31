"""
Skill 生成器 - 根据用户需求生成新 Skill
"""

import os
import re
import logging
from typing import Optional
from datetime import datetime

from core.config import gemini_client, CREATOR_MODEL
from core.skill_loader import skill_loader

logger = logging.getLogger(__name__)

# Skill 模板
SKILL_TEMPLATE = '''"""
{description}
"""
from core.platform.models import UnifiedContext


SKILL_META = {{
    "name": "{name}",
    "description": "{description}",
    "triggers": {triggers},
    "params": {params},
    "version": "1.0.0",
    "author": "{author}"
}}


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行 Skill 逻辑"""
    user_id = ctx.message.user.id
    
{execute_body}
    
    # Must return a string summarizing the result for the Agent
    return "Execution completed."
'''

GENERATION_PROMPT = """你是一个 X-Bot Skill 生成器。根据用户需求生成标准 SKILL.md 格式的技能。

## 用户需求
{requirement}

## 标准 Skill 格式
每个 Skill 包含:
1. **SKILL.md** - 包含 YAML frontmatter 和 Markdown 说明 (必需)
2. **scripts/** - Python 脚本目录 (可选,仅在需要代码时)

## 何时需要 scripts
- 需要 API 调用、HTTP 请求
- 需要数据处理、计算逻辑
- 需要数据库操作
- 需要文件读写

简单的提醒、记录、说明类技能不需要 scripts。

## scripts 中可用工具
- `from repositories import ...` - 数据库操作
- `from services.web_summary_service import fetch_webpage_content` - 网页抓取
- `import httpx` - HTTP 请求 (优先使用,带 timeout)
- `import subprocess` - 允许使用 (仅用于 curl/wget 等必要操作)
- `from core.platform.models import UnifiedContext`
- `await context.run_skill('skill_name', {{'param': 'value'}})` - **关键**: 调用其他技能 (注意 context 是 ctx)

## 安全规则 (仅适用于 scripts)
1. 禁止高危系统命令 (rm -rf, mkfs 等)
2. 禁止修改 data/ 和 downloads/ 以外的文件系统
3. 禁止访问其他用户数据 (必须使用 user_id 隔离)
4. URL 中的用户输入必须使用 urllib.parse.quote 编码
5. 异常必须捕获并返回友好的错误消息
6. **重要**: `execute` 函数必须返回一个字符串 (str) 描述执行结果。

## 函数签名 (必须严格遵守)
```python
from core.platform.models import UnifiedContext

async def execute(ctx: UnifiedContext, params: dict) -> str:
    # 业务逻辑
    return "Result summary"
```

## 输出格式
返回 JSON 格式:
```json
{{
  "skill_md": "SKILL.md 的完整内容,包含 YAML frontmatter",
  "scripts": {{
    "execute.py": "Python 代码内容"
  }},
  "suggested_crontab": "0 8 * * * (可选,仅当需要定时任务时)",
  "suggested_cron_instruction": "Run weather check (可选,仅当需要定时任务时)"
}}
```

如果不需要代码,scripts 可以为空对象 {{}}.

## 完整示例 (代码技能 + 定时)
```json
{{
  "skill_md": "---\\nname: weather_notify\\ndescription: 每天早上8点推送北京天气\\n---\\n\\n# 天气推送\\n\\n每天自动检查天气并推送。",
  "scripts": {{
    "execute.py": "..."
  }},
  "suggested_crontab": "0 8 * * *",
  "suggested_cron_instruction": "Check Beijing weather and send notification"
}}
```

现在,根据用户需求生成技能。返回严格的 JSON 格式,不要添加任何 markdown 代码块标记。"""


async def create_skill(
    requirement: str, user_id: int, skill_name: Optional[str] = None
) -> dict:
    """
    根据需求生成新 Skill (标准 SKILL.md 格式)

    Returns:
        {
            "success": bool,
            "skill_name": str,
            "skill_dir": str,
            "skill_md": str,
            "has_scripts": bool,
            "error": str (if failed)
        }
    """
    try:
        prompt = GENERATION_PROMPT.format(requirement=requirement, user_id=user_id)

        response = await gemini_client.aio.models.generate_content(
            model=CREATOR_MODEL,
            contents=prompt,
        )

        response_text = response.text.strip()

        # 清理可能的 markdown 代码块
        response_text = re.sub(r"^```json\s*", "", response_text)
        response_text = re.sub(r"^```\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)
        response_text = response_text.strip()

        # 解析 JSON 响应
        try:
            import json

            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse JSON response: {e}\nResponse: {response_text[:500]}"
            )
            return {
                "success": False,
                "error": f"AI 返回格式错误,无法解析 JSON: {str(e)}",
            }

        skill_md = data.get("skill_md", "")
        scripts = data.get("scripts", {})
        suggested_crontab = data.get("suggested_crontab")
        suggested_cron_instruction = data.get("suggested_cron_instruction")

        if not skill_md:
            return {"success": False, "error": "生成的技能缺少 SKILL.md 内容"}

        # 从 SKILL.md 中提取 skill_name
        import yaml

        if skill_md.startswith("---"):
            parts = skill_md.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1])
                    extracted_name = frontmatter.get("name", "")
                except Exception as e:
                    logger.warning(f"Failed to parse frontmatter: {e}")
                    extracted_name = ""
            else:
                extracted_name = ""
        else:
            extracted_name = ""

        if not extracted_name:
            extracted_name = (
                skill_name or f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

        # 安全检查 scripts
        if scripts:
            for script_name, script_code in scripts.items():
                security_check = _security_check(script_code)
                if not security_check["safe"]:
                    return {
                        "success": False,
                        "error": f"安全检查失败 ({script_name}): {security_check['reason']}",
                    }

        # 创建技能目录结构
        skills_base = skill_loader.skills_dir
        pending_dir = os.path.join(skills_base, "pending", extracted_name)
        os.makedirs(pending_dir, exist_ok=True)

        # 写入 SKILL.md
        skill_md_path = os.path.join(pending_dir, "SKILL.md")
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(skill_md)

        # 写入 scripts (如果有)
        if scripts:
            scripts_dir = os.path.join(pending_dir, "scripts")
            os.makedirs(scripts_dir, exist_ok=True)

            for script_name, script_code in scripts.items():
                script_path = os.path.join(scripts_dir, script_name)
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(script_code)

        logger.info(f"Generated skill: {extracted_name} -> {pending_dir}")

        return {
            "success": True,
            "skill_name": extracted_name,
            "skill_dir": pending_dir,
            "skill_md": skill_md,
            "has_scripts": bool(scripts),
            "suggested_crontab": suggested_crontab,
            "suggested_cron_instruction": suggested_cron_instruction,
        }

    except Exception as e:
        logger.error(f"Skill creation error: {e}")
        return {"success": False, "error": str(e)}


UPDATE_PROMPT = """你是一个 X-Bot Skill 维护者。请根据用户需求修改现有的 Skill。

## 现有 Skill 信息
**SKILL.md (Metadata)**:
```markdown
{original_skill_md}
```

**Code (scripts/execute.py)**:
```python
{original_code}
```

## 修改需求
{requirement}

## 规则
1. **优先修改 SKILL.md**: 修改描述、触发词等。
2. **定时任务配置**: 如果用户要求修改定时任务，请在返回 JSON 的顶层字段 `suggested_crontab` 中指定。
3. **代码修改**: 只有在业务逻辑需要变更时才修改 Python 代码。
4. **保持完整性**: The returned `skill_md` will replace the file. Keep existing fields.
5. **安全规则**: 遵循 Python 安全编码规范。

## 输出格式
请返回 JSON 格式:
```json
{{
  "skill_md": "修改后的 SKILL.md 完整内容 (YAML 中不应有 crotab)",
  "scripts": {{
      "execute.py": "修改后的 Python 代码 (如果不需要代码可为空字符串或省略)"
  }},
  "suggested_crontab": "0 * * * * (Optional, if cron needs update)",
  "suggested_cron_instruction": "Task instruction"
}}
```
"""


async def update_skill(skill_name: str, requirement: str, user_id: int) -> dict:
    """
    更新现有的 Skill (生成新代码并存入 pending)
    支持 standard (SKILL.md + optional scripts) 和 legacy (.py)
    """
    try:
        # 1. 查找现有 Skill
        skill_info = skill_loader.get_skill(skill_name)

        if not skill_info:
            return {"success": False, "error": f"Skill '{skill_name}' not found."}

        # 🔒 安全检查：禁止修改 builtin 技能
        source = skill_info.get("source", "")
        if source == "builtin":
            logger.warning(
                f"[update_skill] Blocked attempt to modify builtin skill: {skill_name}"
            )
            return {
                "success": False,
                "error": "🔒 系统技能受保护，无法修改。请联系管理员。",
            }

        skill_type = skill_info.get("skill_type")
        original_code = ""
        original_skill_md = ""
        is_standard = False

        # 确定代码位置和读取原始内容
        if skill_type == "standard":
            is_standard = True
            skill_dir = skill_info.get("skill_dir")

            # Read SKILL.md
            md_path = skill_info.get("skill_md_path")
            if md_path and os.path.exists(md_path):
                with open(md_path, "r", encoding="utf-8") as f:
                    original_skill_md = f.read()

            # Read execute.py (if exists)
            script_path = os.path.join(skill_dir, "scripts", "execute.py")
            if os.path.exists(script_path):
                with open(script_path, "r", encoding="utf-8") as f:
                    original_code = f.read()
            else:
                original_code = "(No existing code)"

        elif skill_type == "legacy":
            original_path = skill_info["path"]
            with open(original_path, "r", encoding="utf-8") as f:
                original_code = f.read()
            original_skill_md = "(Legacy skill, no separate SKILL.md)"
        else:
            return {
                "success": False,
                "error": f"不支持更新类型为 {skill_type} 的技能。",
            }

        # 2. 生成新内容
        prompt = UPDATE_PROMPT.format(
            original_skill_md=original_skill_md,
            original_code=original_code,
            requirement=requirement,
        )

        response = await gemini_client.aio.models.generate_content(
            model=CREATOR_MODEL,
            contents=prompt,
        )

        response_text = response.text.strip()

        # 清理 JSON
        response_text = re.sub(r"^```json\s*", "", response_text)
        response_text = re.sub(r"^```\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)
        response_text = response_text.strip()

        import json

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"AI 返回格式错误: {e}"}

        new_skill_md = data.get("skill_md", "")
        new_scripts = data.get("scripts", {})
        suggested_crontab = data.get("suggested_crontab")
        suggested_cron_instruction = data.get("suggested_cron_instruction")

        if is_standard and not new_skill_md:
            # If AI didn't return MD, usage original? No, safest is to fail or warn.
            # Or maybe it's a code-only update? But prompt requires MD.
            pass

        # 3. 验证与安全检查 (for new code)
        new_code = new_scripts.get("execute.py", "")
        if new_code and new_code != "(No existing code)":
            security_check = _security_check(new_code)
            if not security_check["safe"]:
                return {
                    "success": False,
                    "error": f"安全检查失败: {security_check['reason']}",
                }

        # 4. 保存到 pending
        skills_base = skill_loader.skills_dir
        pending_base = os.path.join(skills_base, "pending")
        os.makedirs(pending_base, exist_ok=True)

        filepath = ""  # return value

        if is_standard:
            # 标准模式
            pending_skill_dir = os.path.join(pending_base, skill_name)

            # Clean pending
            if os.path.exists(pending_skill_dir):
                import shutil

                shutil.rmtree(pending_skill_dir)

            # Copy original dir first to preserve other assets
            import shutil

            shutil.copytree(skill_dir, pending_skill_dir, dirs_exist_ok=True)

            # Overwrite SKILL.md
            if new_skill_md:
                with open(
                    os.path.join(pending_skill_dir, "SKILL.md"), "w", encoding="utf-8"
                ) as f:
                    f.write(new_skill_md)

            # Overwrite execute.py
            if new_code and new_code.strip():
                script_dir = os.path.join(pending_skill_dir, "scripts")
                os.makedirs(script_dir, exist_ok=True)
                with open(
                    os.path.join(script_dir, "execute.py"), "w", encoding="utf-8"
                ) as f:
                    f.write(new_code)

            filepath = os.path.join(pending_skill_dir, "SKILL.md")
            code_preview = (
                new_skill_md[:200] + "..."
            )  # Use MD as preview if code is empty
            if new_code:
                code_preview = new_code

        else:
            # Legacy 模式 (Usually returns new code)
            # If AI returns JSON for legacy, we just look for scripts output or maybe it converted to standard?
            # For now assume legacy stays legacy or we block legacy updates.
            pass

        logger.info(f"Generated skill update: {skill_name} -> {filepath}")

        return {
            "success": True,
            "skill_name": skill_name,
            "filepath": filepath,
            "code": code_preview
            if "code_preview" in locals()
            else "Updated successfully.",
            "suggested_crontab": suggested_crontab,
            "suggested_cron_instruction": suggested_cron_instruction,
        }

    except Exception as e:
        logger.error(f"Skill update error: {e}")
        return {"success": False, "error": str(e)}


def _security_check(code: str) -> dict:
    """
    代码安全检查
    """
    # 危险模式
    dangerous_patterns = [
        (r"\bos\.system\b", "禁止使用 os.system"),
        (r"\bsubprocess\b", "禁止使用 subprocess"),
        (r"\beval\b", "禁止使用 eval"),
        (r"\bexec\b", "禁止使用 exec"),
        (r"\b__import__\b", "禁止使用 __import__"),
        (r'\bopen\s*\([^)]*["\']/', "禁止访问绝对路径文件"),
        (r"\bshutil\b", "禁止使用 shutil"),
    ]

    for pattern, reason in dangerous_patterns:
        if re.search(pattern, code):
            return {"safe": False, "reason": reason}

    return {"safe": True, "reason": "OK"}


async def approve_skill(skill_name: str) -> dict:
    """
    审核通过 Skill，从 pending 移动到 learned
    支持目录结构和旧版 .py 文件
    并修正文件权限以匹配 builtin 目录
    """
    skills_base = skill_loader.skills_dir
    pending_dir_path = os.path.join(skills_base, "pending", skill_name)
    pending_file_path = os.path.join(skills_base, "pending", f"{skill_name}.py")
    builtin_dir = os.path.join(skills_base, "builtin")

    # 检查是目录还是文件
    is_directory = os.path.isdir(pending_dir_path)
    is_file = os.path.isfile(pending_file_path)

    if not is_directory and not is_file:
        return {"success": False, "error": f"Skill {skill_name} 不存在于待审核列表"}

    if is_directory:
        # 新格式: 移动整个目录
        learned_path = os.path.join(skills_base, "learned", skill_name)
        import shutil

        if os.path.exists(learned_path):
            shutil.rmtree(learned_path)
        shutil.move(pending_dir_path, learned_path)

        # 递归修正权限
        try:
            if os.path.exists(builtin_dir):
                st = os.stat(builtin_dir)
                target_uid = st.st_uid
                target_gid = st.st_gid

                for root, dirs, files in os.walk(learned_path):
                    os.chown(root, target_uid, target_gid)
                    for d in dirs:
                        os.chown(os.path.join(root, d), target_uid, target_gid)
                    for f in files:
                        os.chown(os.path.join(root, f), target_uid, target_gid)

                logger.info(
                    f"Fixed permissions for {skill_name}: {target_uid}:{target_gid}"
                )
        except Exception as e:
            logger.warning(f"Failed to fix permissions for {skill_name}: {e}")
    else:
        # 旧格式: 移动单个文件
        learned_path = os.path.join(skills_base, "learned", f"{skill_name}.py")
        os.makedirs(os.path.dirname(learned_path), exist_ok=True)
        os.rename(pending_file_path, learned_path)

        try:
            if os.path.exists(builtin_dir):
                st = os.stat(builtin_dir)
                target_uid = st.st_uid
                target_gid = st.st_gid
                os.chown(learned_path, target_uid, target_gid)
                logger.info(
                    f"Fixed permissions for {skill_name}: {target_uid}:{target_gid}"
                )
        except Exception as e:
            logger.warning(f"Failed to fix permissions for {skill_name}: {e}")

    # 刷新加载器索引
    # 刷新加载器索引
    skill_loader.scan_skills()

    logger.info(f"Approved skill: {skill_name}")
    return {"success": True, "path": learned_path}


async def reject_skill(skill_name: str) -> dict:
    """
    拒绝 Skill，删除 pending 目录或文件
    """
    skills_base = skill_loader.skills_dir
    pending_dir_path = os.path.join(skills_base, "pending", skill_name)
    pending_file_path = os.path.join(skills_base, "pending", f"{skill_name}.py")

    if os.path.isdir(pending_dir_path):
        import shutil

        shutil.rmtree(pending_dir_path)
        logger.info(f"Rejected skill directory: {skill_name}")
        return {"success": True}
    elif os.path.isfile(pending_file_path):
        os.remove(pending_file_path)
        logger.info(f"Rejected skill file: {skill_name}")
        return {"success": True}
    else:
        return {"success": False, "error": f"Skill {skill_name} 不存在"}


def list_pending_skills() -> list[dict]:
    """
    列出待审核的 Skills (支持目录和文件)
    """
    skills_dir = os.path.join(skill_loader.skills_dir, "pending")

    if not os.path.exists(skills_dir):
        return []

    result = []
    for entry in os.listdir(skills_dir):
        if entry.startswith("_"):
            continue

        entry_path = os.path.join(skills_dir, entry)

        # 目录格式 (新)
        if os.path.isdir(entry_path):
            result.append(
                {
                    "name": entry,
                    "path": entry_path,
                    "type": "directory",
                    "created_at": datetime.fromtimestamp(os.path.getctime(entry_path)),
                }
            )
        # 文件格式 (旧)
        elif entry.endswith(".py"):
            result.append(
                {
                    "name": entry[:-3],
                    "path": entry_path,
                    "type": "file",
                    "created_at": datetime.fromtimestamp(os.path.getctime(entry_path)),
                }
            )

    return result


async def adopt_skill(content: str, user_id: int) -> dict:
    """
    Adopt an existing skill content (install from URL) into pending for review.
    Supports both standard SKILL.md and legacy .py content.
    """
    try:
        skill_name = ""
        is_standard = False

        # 1. Detect Type & Extract Name
        if content.startswith("---"):
            is_standard = True
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

        elif "SKILL_META" in content:
            is_standard = False
            # Regex parse SKILL_META
            match = re.search(
                r'SKILL_META\s*=\s*{[^}]*"name":\s*"([^"]+)"', content, re.DOTALL
            )
            if match:
                skill_name = match.group(1)
            else:
                # Fallback: simple string search
                pass

        if not skill_name:
            # Try stricter regex or fail
            match_name = re.search(r'"name":\s*"([a-zA-Z0-9_]+)"', content)
            if match_name:
                skill_name = match_name.group(1)
            else:
                return {
                    "success": False,
                    "error": "Could not extract 'name' from skill content.",
                }

        # 2. Save to Pending
        skills_base = skill_loader.skills_dir
        pending_base = os.path.join(skills_base, "pending")
        os.makedirs(pending_base, exist_ok=True)

        if is_standard:
            # Create directory
            skill_dir = os.path.join(pending_base, skill_name)
            os.makedirs(skill_dir, exist_ok=True)

            # Save SKILL.md
            # Note: If adopting SKILL.md, we might strictly only have the MD file.
            # If the skill requires scripts, this single-file adopt is insufficient unless we download the zip/repo.
            # But for now, we assume single-file SKILL.md or user will add scripts later?
            # Or the user provided a URL to SKILL.md, we save it.

            md_path = os.path.join(skill_dir, "SKILL.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)

            filepath = md_path
        else:
            # Legacy .py
            filepath = os.path.join(pending_base, f"{skill_name}.py")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

        logger.info(f"Adopted skill: {skill_name} -> {filepath}")

        return {"success": True, "skill_name": skill_name, "path": filepath}

    except Exception as e:
        logger.error(f"Adopt skill error: {e}")
        return {"success": False, "error": str(e)}
