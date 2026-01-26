"""
Skill 执行器 - 协调 AI 理解和执行标准 Skill
"""
import logging
from typing import Optional, Dict, Any, Tuple, AsyncGenerator

from core.config import gemini_client, GEMINI_MODEL
from core.skill_loader import skill_loader
from services.sandbox_executor import sandbox_executor

logger = logging.getLogger(__name__)

# 标准 Skill 执行的系统提示
SKILL_EXECUTION_PROMPT = """你是一个专业的技术助手。你正在执行一个特定的技能任务。

## 你拥有的能力 (来自 Skill 文档)

{skill_content}

## 可用的辅助脚本

{scripts_list}

## 任务

根据用户的请求，使用上述文档中的知识来完成任务。

如果需要执行代码，请按以下格式输出：

```python
# 你的 Python 代码
```

## 沙箱限制 (重要!)

代码将在安全沙箱中执行，以下是限制：

1. **禁止使用**: subprocess, os.system, eval, exec, socket, urllib
2. **网络请求**: 必须使用 `httpx` 库 (已安装)，不能用 curl 或 subprocess
3. **文件操作**: 只能在当前目录读写文件

如果文档中使用了 curl 或 shell 命令示例，请将其转换为等效的 Python/httpx 代码。

## 网络可靠性提示

- **wttr.in 可能有 SSL 问题**，优先使用 **open-meteo.com** API
- 对于天气查询，推荐使用: `https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON&current_weather=true`
- 需要先通过地名获取经纬度，可以用: `https://geocoding-api.open-meteo.com/v1/search?name=城市名&count=1`
- 所有请求加上 `timeout=30`

示例 (天气查询):
```python
import httpx

# 1. 获取城市坐标
geo = httpx.get("https://geocoding-api.open-meteo.com/v1/search?name=Quzhou&count=1", timeout=30).json()
lat, lon = geo["results"][0]["latitude"], geo["results"][0]["longitude"]

# 2. 获取天气
weather = httpx.get(f"https://api.open-meteo.com/v1/forecast?latitude={{lat}}&longitude={{lon}}&current_weather=true", timeout=30).json()
print(f"温度: {{weather['current_weather']['temperature']}}°C")
```

## 用户请求

{user_request}

## 附加上下文

{extra_context}
"""


class SkillExecutor:
    """
    标准 Skill 执行器
    
    工作流程：
    1. 加载 SKILL.md 内容
    2. 将内容注入到 AI 上下文
    3. AI 理解任务并生成解决方案（可能包含代码）
    4. 如果有代码，在沙箱中执行
    5. 返回结果
    """
    
    async def execute_standard_skill(
        self,
        skill_name: str,
        user_request: str,
        extra_context: str = "",
        input_files: Dict[str, bytes] = None,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict[str, bytes]]], None]:
        """
        执行标准 Skill
        
        Yields:
            (status_message, output_files)
        """
        # 1. 获取 Skill 信息
        skill_info = skill_loader.get_skill(skill_name)
        if not skill_info:
            yield f"❌ 找不到技能: {skill_name}", None
            return
        
        if skill_info.get("skill_type") != "standard":
            yield f"❌ {skill_name} 不是标准协议技能", None
            return
        
        skill_content = skill_info.get("skill_md_content", "")
        skill_dir = skill_info.get("skill_dir", "")
        scripts = skill_info.get("scripts", [])
        
        yield f"📚 正在使用技能 **{skill_name}** 处理您的请求...", None
        
        # 2. 构建提示
        scripts_list = "\n".join([f"- {s}" for s in scripts]) if scripts else "无"
        
        prompt = SKILL_EXECUTION_PROMPT.format(
            skill_content=skill_content[:8000],  # 截断过长内容
            scripts_list=scripts_list,
            user_request=user_request,
            extra_context=extra_context or "无",
        )
        
        # 3. 调用 AI 生成解决方案
        yield "🤔 正在分析任务...", None
        
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "system_instruction": "你是一个代码执行助手。根据技能文档完成用户任务。如果需要生成文件，务必使用代码实现。请注意：你返回的所有非代码文本内容将直接作为 Telegram Bot 的回复发送给用户，请保持语气友好、简洁，并使用 Markdown 格式。",
                },
            )
            
            if not response.text:
                yield "❌ AI 无法生成解决方案", None
                return
            
            ai_response = response.text
            
        except Exception as e:
            logger.error(f"AI generation error: {e}")
            yield f"❌ AI 服务错误: {e}", None
            return
        
        # 4. 检查是否包含代码块
        import re
        code_blocks = re.findall(r"```python\n(.*?)```", ai_response, re.DOTALL)
        
        if code_blocks:
            yield "⚙️ 正在执行代码...", None
            
            # 执行所有代码块
            all_output_files = {}
            execution_output = ""
            
            for i, code in enumerate(code_blocks):
                success, output, output_files = await sandbox_executor.execute_code(
                    code=code,
                    input_files=input_files,
                    skill_dir=skill_dir,
                )
                
                execution_output += f"\n[代码块 {i+1}]\n{output}\n"
                all_output_files.update(output_files)
                
                if not success:
                    yield f"⚠️ 代码执行出现问题:\n```\n{output}\n```", None
            
            # 5. 返回结果
            if all_output_files:
                yield f"✅ 执行完成！生成了 {len(all_output_files)} 个文件。", all_output_files
            else:
                # 没有生成文件，返回 AI 的文字回复
                # 移除代码块，只保留解释文字
                clean_response = re.sub(r"```python\n.*?```", "[已执行]", ai_response, flags=re.DOTALL)
                yield clean_response, None
                
                if execution_output.strip():
                    yield f"\n📋 执行输出:\n```\n{execution_output.strip()}\n```", None
        else:
            # 没有代码，直接返回 AI 回复
            yield ai_response, None
    
    async def execute_skill(
        self,
        skill_name: str,
        user_request: str,
        **kwargs
    ) -> AsyncGenerator[Tuple[str, Optional[Dict[str, bytes]]], None]:
        """
        统一执行入口 - 自动判断 Skill 类型
        """
        skill_info = skill_loader.get_skill(skill_name)
        
        if not skill_info:
            yield f"❌ 找不到技能: {skill_name}", None
            return
        
        if skill_info.get("skill_type") == "standard":
            async for msg, files in self.execute_standard_skill(skill_name, user_request, **kwargs):
                yield msg, files
        else:
            # 旧版 skill 不在这里处理，应该在 handler 层直接调用 module.execute()
            yield f"⚠️ {skill_name} 是旧版技能，需要使用 legacy executor", None


# 全局单例
skill_executor = SkillExecutor()
