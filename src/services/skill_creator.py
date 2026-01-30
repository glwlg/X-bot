"""
Skill 生成器 - 根据用户需求生成新 Skill
"""
import os
import re
import logging
from typing import Optional
from datetime import datetime

from core.config import gemini_client, CREATOR_MODEL, DATA_DIR

logger = logging.getLogger(__name__)

# Skill 模板
SKILL_TEMPLATE = '''"""
{description}
"""
from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text


SKILL_META = {{
    "name": "{name}",
    "description": "{description}",
    "triggers": {triggers},
    "params": {params},
    "version": "1.0.0",
    "author": "{author}"
}}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    """执行 Skill 逻辑"""
    user_id = update.effective_user.id
    
{execute_body}
'''

GENERATION_PROMPT = '''你是一个 X-Bot Skill 生成器。根据用户需求生成标准 SKILL.md 格式的技能。

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
- `from utils import smart_reply_text, smart_edit_text` - 消息发送
- `from services.web_summary_service import fetch_webpage_content` - 网页抓取
- `import httpx` - HTTP 请求
- `from telegram import Update`
- `from telegram.ext import ContextTypes`

## 安全规则 (仅适用于 scripts)
1. 禁止执行系统命令 (os.system, subprocess)
2. 禁止修改文件系统 (除了 data/ 目录)
3. 禁止访问其他用户数据 (必须使用 user_id 隔离)
4. URL 中的用户输入必须使用 urllib.parse.quote 编码
5. HTTP 请求必须设置 timeout
6. 异常必须捕获并返回友好的错误消息

## 输出格式
返回 JSON 格式:
```json
{{
  "skill_md": "SKILL.md 的完整内容,包含 YAML frontmatter",
  "scripts": {{
    "execute.py": "Python 代码内容"
  }}
}}
```

如果不需要代码,scripts 可以为空对象 {{}}.

## SKILL.md 示例 (不需要代码的简单技能)
```markdown
---
name: daily_reminder
description: 每日提醒功能,帮助用户记住重要事项
---

# 每日提醒

这个技能帮助用户设置和管理每日提醒。

## 使用方法

用户可以通过以下方式使用:
- "提醒我每天喝水"
- "设置每日提醒"
- "取消提醒"

## 功能说明

Bot 会记住用户的提醒需求,并在适当时候发送提醒消息。
```

## 完整示例 (需要代码的复杂技能)
```json
{{
  "skill_md": "---\\nname: weather_query\\ndescription: 查询天气信息,支持国内外主要城市\\n---\\n\\n# 天气查询\\n\\n查询指定城市的天气信息。\\n\\n## 使用方法\\n\\n- \\"北京天气\\"\\n- \\"上海天气怎么样\\"\\n- \\"查询深圳天气\\"\\n\\n## 实现\\n\\n使用 `scripts/execute.py` 调用天气 API 获取实时数据。",
  "scripts": {{
    "execute.py": "\\"\\"\\"\\"\\n天气查询 Skill\\n\\"\\"\\"\\"\\"\\nimport httpx\\nfrom telegram import Update\\nfrom telegram.ext import ContextTypes\\nfrom utils import smart_reply_text\\nimport urllib.parse\\n\\n\\nasync def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:\\n    \\"\\"\\"执行天气查询\\"\\"\\"\\n    user_id = update.effective_user.id\\n    city = params.get(\\"city\\", \\"北京\\")\\n    \\n    try:\\n        # URL 编码\\n        encoded_city = urllib.parse.quote(city)\\n        url = f\\"https://api.example.com/weather?city={{encoded_city}}\\"\\n        \\n        async with httpx.AsyncClient(timeout=10.0) as client:\\n            response = await client.get(url)\\n            response.raise_for_status()\\n            data = response.json()\\n            \\n        weather = data.get(\\"weather\\", \\"未知\\")\\n        temp = data.get(\\"temperature\\", \\"N/A\\")\\n        \\n        await smart_reply_text(update, f\\"🌤️ {{city}} 天气: {{weather}}, 温度: {{temp}}°C\\")\\n        \\n    except Exception as e:\\n        await smart_reply_text(update, f\\"❌ 查询失败: {{str(e)}}\\")\\n"
  }}
}}
```

现在,根据用户需求生成技能。如果是简单需求,只生成 SKILL.md;如果需要代码,同时生成 scripts/execute.py。
返回严格的 JSON 格式,不要添加任何 markdown 代码块标记。'''


async def create_skill(
    requirement: str, 
    user_id: int,
    skill_name: Optional[str] = None
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
        prompt = GENERATION_PROMPT.format(
            requirement=requirement,
            user_id=user_id
        )
        
        response = await gemini_client.aio.models.generate_content(
            model=CREATOR_MODEL,
            contents=prompt,
        )
        
        response_text = response.text.strip()
        
        # 清理可能的 markdown 代码块
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'^```\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        response_text = response_text.strip()
        
        # 解析 JSON 响应
        try:
            import json
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}\nResponse: {response_text[:500]}")
            return {
                "success": False,
                "error": f"AI 返回格式错误,无法解析 JSON: {str(e)}"
            }
        
        skill_md = data.get("skill_md", "")
        scripts = data.get("scripts", {})
        
        if not skill_md:
            return {
                "success": False,
                "error": "生成的技能缺少 SKILL.md 内容"
            }
        
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
            extracted_name = skill_name or f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 安全检查 scripts
        if scripts:
            for script_name, script_code in scripts.items():
                security_check = _security_check(script_code)
                if not security_check["safe"]:
                    return {
                        "success": False,
                        "error": f"安全检查失败 ({script_name}): {security_check['reason']}"
                    }
        
        # 创建技能目录结构
        skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
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
            "has_scripts": bool(scripts)
        }
        
    except Exception as e:
        logger.error(f"Skill creation error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


UPDATE_PROMPT = '''你是一个 X-Bot Skill 维护者。请根据用户需求修改现有的 Skill 代码。

## 原代码
```python
{original_code}
```

## 修改需求
{requirement}

## 规则
1. 保持原有的 `SKILL_META` 结构，并在 `description` 中简要说明修改内容，版本号 `version` +0.0.1。
2. 保持 `execute` 函数签名不变。
3. 遵循相同的安全和代码质量规则（禁止系统命令，URL编码等）。
4. 只返回完整的、修改后的 Python 代码。

## 输出
```python
...
```
'''


async def update_skill(
    skill_name: str,
    requirement: str,
    user_id: int
) -> dict:
    """
    更新现有的 Skill (生成新代码并存入 pending)
    """
    try:
        # 1. 查找现有 Skill
        skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
        learned_path = os.path.join(skills_base, "learned", f"{skill_name}.py")
        
        # 也可以支持 builtin，但修改后会变成 learned (覆盖)
        # 暂时只查找 learned，或者通过 SkillLoader 查找路径
        from core.skill_loader import skill_loader
        skill_info = skill_loader.get_skill(skill_name)
        
        if not skill_info:
            return {"success": False, "error": f"Skill '{skill_name}' not found."}
            
        # 如果是 legacy skill (.py)
        if skill_info["skill_type"] == "legacy":
            original_path = skill_info["path"]
            with open(original_path, "r", encoding="utf-8") as f:
                original_code = f.read()
        else:
            return {"success": False, "error": "目前仅支持修改 Python (Legacy) 格式的 Skill。"}

        # 2. 生成新代码
        prompt = UPDATE_PROMPT.format(
            original_code=original_code,
            requirement=requirement
        )
        
        response = await gemini_client.aio.models.generate_content(
            model=CREATOR_MODEL,
            contents=prompt,
        )
        
        code = response.text.strip()
        
        # 清理 markdown
        code = re.sub(r'^```python\s*', '', code)
        code = re.sub(r'^```\s*', '', code)
        code = re.sub(r'\s*```$', '', code)
        code = code.strip()
        
        # 3. 验证与安全检查
        if "SKILL_META" not in code or "async def execute" not in code:
            return {"success": False, "error": "生成的代码结构不正确"}
            
        security_check = _security_check(code)
        if not security_check["safe"]:
            return {"success": False, "error": f"安全检查失败: {security_check['reason']}"}
            
        # 4. 保存到 pending
        skills_dir = os.path.join(skills_base, "pending")
        os.makedirs(skills_dir, exist_ok=True)
        
        filename = f"{skill_name}.py"
        filepath = os.path.join(skills_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
            
        logger.info(f"Generated skill update: {skill_name} -> {filepath}")
        
        return {
            "success": True,
            "skill_name": skill_name,
            "filepath": filepath,
            "code": code
        }

    except Exception as e:
        logger.error(f"Skill update error: {e}")
        return {
            "success": False,
            "error": str(e)
        }



def _security_check(code: str) -> dict:
    """
    代码安全检查
    """
    # 危险模式
    dangerous_patterns = [
        (r'\bos\.system\b', "禁止使用 os.system"),
        (r'\bsubprocess\b', "禁止使用 subprocess"),
        (r'\beval\b', "禁止使用 eval"),
        (r'\bexec\b', "禁止使用 exec"),
        (r'\b__import__\b', "禁止使用 __import__"),
        (r'\bopen\s*\([^)]*["\']/', "禁止访问绝对路径文件"),
        (r'\bshutil\b', "禁止使用 shutil"),
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
    skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
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
                        
                logger.info(f"Fixed permissions for {skill_name}: {target_uid}:{target_gid}")
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
                logger.info(f"Fixed permissions for {skill_name}: {target_uid}:{target_gid}")
        except Exception as e:
            logger.warning(f"Failed to fix permissions for {skill_name}: {e}")
    
    # 刷新加载器索引
    from core.skill_loader import skill_loader
    skill_loader.scan_skills()
    
    logger.info(f"Approved skill: {skill_name}")
    return {"success": True, "path": learned_path}


async def reject_skill(skill_name: str) -> dict:
    """
    拒绝 Skill，删除 pending 目录或文件
    """
    skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
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
    skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills", "pending")
    
    if not os.path.exists(skills_dir):
        return []
    
    result = []
    for entry in os.listdir(skills_dir):
        if entry.startswith("_"):
            continue
            
        entry_path = os.path.join(skills_dir, entry)
        
        # 目录格式 (新)
        if os.path.isdir(entry_path):
            result.append({
                "name": entry,
                "path": entry_path,
                "type": "directory",
                "created_at": datetime.fromtimestamp(os.path.getctime(entry_path))
            })
        # 文件格式 (旧)
        elif entry.endswith(".py"):
            result.append({
                "name": entry[:-3],
                "path": entry_path,
                "type": "file",
                "created_at": datetime.fromtimestamp(os.path.getctime(entry_path))
            })
    
    return result
