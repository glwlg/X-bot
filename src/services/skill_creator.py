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

GENERATION_PROMPT = '''你是一个 X-Bot Skill 生成器。根据用户需求生成 Python 代码。

## 用户需求
{requirement}

## Skill 规范
每个 Skill 必须包含:
1. `SKILL_META` 字典定义元数据
2. `async def execute(update, context, params)` 函数

## 可用工具
- `from repositories import ...` - 数据库操作
- `from utils import smart_reply_text, smart_edit_text` - 消息发送
- `from services.web_summary_service import fetch_webpage_content` - 网页抓取
- `import httpx` - HTTP 请求

## 安全规则
1. 禁止执行系统命令 (os.system, subprocess)
2. 禁止修改文件系统 (除了 data/ 目录)
3. 禁止访问其他用户数据 (必须使用 user_id 隔离)

## 代码质量规则
1. URL 中的用户输入必须使用 urllib.parse.quote 编码
2. URL 字符串中不要包含换行符 \\n，使用空格或 + 分隔
3. HTTP 请求必须设置 timeout
4. 异常必须捕获并返回友好的错误消息

## 输出格式
只返回纯 Python 代码，不要 markdown 代码块。

## 示例
```python
"""
签到 Skill - 帮用户签到
"""
from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text


SKILL_META = {{
    "name": "checkin",
    "description": "自动签到功能",
    "triggers": ["签到", "checkin", "打卡"],
    "params": {{
        "site": {{
            "type": "str",
            "description": "签到站点"
        }}
    }},
    "version": "1.0.0",
    "author": "{user_id}"
}}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    user_id = update.effective_user.id
    site = params.get("site", "")
    
    await smart_reply_text(update, f"✅ 正在为您签到: {{site}}")
    # 实现签到逻辑...
```

现在，根据用户需求生成代码：'''


async def create_skill(
    requirement: str, 
    user_id: int,
    skill_name: Optional[str] = None
) -> dict:
    """
    根据需求生成新 Skill
    
    Returns:
        {
            "success": bool,
            "skill_name": str,
            "filepath": str,
            "code": str,
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
        
        code = response.text.strip()
        
        # 清理 markdown 代码块
        code = re.sub(r'^```python\s*', '', code)
        code = re.sub(r'^```\s*', '', code)
        code = re.sub(r'\s*```$', '', code)
        code = code.strip()
        
        # 验证代码结构
        if "SKILL_META" not in code or "async def execute" not in code:
            return {
                "success": False,
                "error": "生成的代码缺少 SKILL_META 或 execute 函数"
            }
        
        # 安全检查
        security_check = _security_check(code)
        if not security_check["safe"]:
            return {
                "success": False,
                "error": f"安全检查失败: {security_check['reason']}"
            }
        
        # 提取 skill_name
        meta_match = re.search(r'"name"\s*:\s*"([^"]+)"', code)
        if meta_match:
            extracted_name = meta_match.group(1)
        else:
            extracted_name = skill_name or f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 保存到 pending 目录
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills", "pending")
        os.makedirs(skills_dir, exist_ok=True)
        
        filename = f"{extracted_name}.py"
        filepath = os.path.join(skills_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        
        logger.info(f"Generated skill: {extracted_name} -> {filepath}")
        
        return {
            "success": True,
            "skill_name": extracted_name,
            "filepath": filepath,
            "code": code
        }
        
    except Exception as e:
        logger.error(f"Skill creation error: {e}")
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
    """
    skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
    pending_path = os.path.join(skills_base, "pending", f"{skill_name}.py")
    learned_path = os.path.join(skills_base, "learned", f"{skill_name}.py")
    
    if not os.path.exists(pending_path):
        return {"success": False, "error": f"Skill {skill_name} 不存在于待审核列表"}
    
    os.makedirs(os.path.dirname(learned_path), exist_ok=True)
    os.rename(pending_path, learned_path)
    
    # 刷新加载器索引
    from core.skill_loader import skill_loader
    from core.skill_router import skill_router
    
    skill_loader.scan_skills()
    skill_router.invalidate_cache()
    
    logger.info(f"Approved skill: {skill_name}")
    return {"success": True, "filepath": learned_path}


async def reject_skill(skill_name: str) -> dict:
    """
    拒绝 Skill，删除 pending 文件
    """
    skills_base = os.path.join(os.path.dirname(__file__), "..", "skills")
    pending_path = os.path.join(skills_base, "pending", f"{skill_name}.py")
    
    if not os.path.exists(pending_path):
        return {"success": False, "error": f"Skill {skill_name} 不存在"}
    
    os.remove(pending_path)
    logger.info(f"Rejected skill: {skill_name}")
    return {"success": True}


def list_pending_skills() -> list[dict]:
    """
    列出待审核的 Skills
    """
    skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills", "pending")
    
    if not os.path.exists(skills_dir):
        return []
    
    result = []
    for filename in os.listdir(skills_dir):
        if filename.endswith(".py") and not filename.startswith("_"):
            filepath = os.path.join(skills_dir, filename)
            result.append({
                "name": filename[:-3],
                "filepath": filepath,
                "created_at": datetime.fromtimestamp(os.path.getctime(filepath))
            })
    
    return result
