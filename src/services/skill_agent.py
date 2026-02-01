"""
Skill Agent - 智能执行代理
"""

import logging
import asyncio
import json
from typing import Optional, Dict, Any, Tuple, AsyncGenerator

from core.config import gemini_client, GEMINI_MODEL
from core.skill_loader import skill_loader
from services.sandbox_executor import sandbox_executor
from core.prompts import SKILL_AGENT_DECISION_PROMPT

logger = logging.getLogger(__name__)


class SkillDelegationRequest:
    """Delegation Request Object"""

    def __init__(self, target_skill: str, instruction: str):
        self.target_skill = target_skill
        self.instruction = instruction

    def __str__(self):
        return f"[Delegation -> {self.target_skill}: {self.instruction}]"


class SkillAgent:
    """
    Skill Agent
    Replaces SkillExecutor. Uses LLM to decide between EXECUTE, DELEGATE, or REPLY.
    """

    async def execute_skill(
        self,
        skill_name: str,
        user_request: str,
        extra_context: str = "",
        input_files: Dict[str, bytes] = None,
        ctx: Any = None,
        **kwargs,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict[str, bytes]], Any], None]:
        """
        Execute skill logic via Agent thinking.
        Yields: (status_msg, files, result_object)

        result_object can be SkillDelegationRequest.
        """

        # 1. Load context and documentation
        skill_info = skill_loader.get_skill(skill_name)
        if not skill_info:
            yield f"❌ 找不到技能: {skill_name}", None, None
            return

        skill_content = skill_info.get("skill_md_content", "")
        skill_dir = skill_info.get("skill_dir", "")

        # 2. Think (Decision Making)
        yield f"🧠 SkillAgent ({skill_name}) 正在思考...", None, None

        decision = await self._think(
            skill_name, skill_content, user_request, extra_context
        )

        action = decision.get("action")
        logger.info(f"SkillAgent Decision: {action} - {decision}")

        # 3. Act based on decision
        if action == "REPLY":
            content = decision.get("content", "")
            yield content, None, None
            return

        elif action == "DELEGATE":
            target = decision.get("target_skill")
            instruction = decision.get("instruction")
            delegation = SkillDelegationRequest(target, instruction)
            yield f"👉 委托给 `{target}`...", None, delegation
            return

        elif action == "EXECUTE":
            execute_type = decision.get("execute_type")
            content = decision.get("content")

            if execute_type == "SCRIPT":
                # Run execute.py
                async for msg, files, _ in self._run_script(
                    skill_name, skill_dir, content, ctx
                ):
                    yield msg, files, None

            elif execute_type == "COMMAND":
                # Run shell command directly
                yield f"⚙️ 正在执行 Shell 命令...", None, None

                success, output = await sandbox_executor.execute_shell_command(
                    command=content, skill_dir=skill_dir
                )

                if output.strip():
                    yield f"📋 执行输出:\n```\n{output}\n```", None, None
                else:
                    yield "✅ 执行完成 (无输出)。", None, None

            elif execute_type == "CODE":
                # Run generated python code
                yield f"⚙️ 正在执行代码 (CODE)...", None, None

                success, output, output_files = await sandbox_executor.execute_code(
                    code=content, input_files=input_files, skill_dir=skill_dir
                )

                if output_files:
                    yield (
                        f"✅ 执行完成，生成 {len(output_files)} 个文件。",
                        output_files,
                        None,
                    )

                if output.strip():
                    yield f"📋 执行输出:\n```\n{output}\n```", None, None
                else:
                    yield "✅ 执行完成。", None, None

            else:
                yield f"❌ 未知执行类型: {execute_type}", None, None

        else:
            yield f"❌ Agent 决策无效: {action}", None, None

    async def _think(
        self, skill_name, skill_content, user_request, extra_context
    ) -> Dict[str, Any]:
        """Call LLM to decide action"""
        prompt = SKILL_AGENT_DECISION_PROMPT.format(
            skill_content=skill_content[:20000],
            user_request=user_request,
            extra_context=extra_context,
        )

        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            text = response.text
            if not text:
                logger.error("Thinking failed: Empty response from AI")
                return {"action": "REPLY", "content": "决策系统故障: AI 返回了空响应。"}

            # Clean markdown code blocks if present (just in case)
            if text.startswith("```"):
                import re

                text = re.sub(r"^```json\s*", "", text)
                text = re.sub(r"^```\s*", "", text)
                text = re.sub(r"\s*```$", "", text)

            data = json.loads(text)

            # Robustness: Handle if LLM returns a list [ {action...} ]
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    data = data[0]
                else:
                    return {
                        "action": "REPLY",
                        "content": f"决策格式错误: AI 返回了列表但无法解析: {text[:100]}",
                    }

            if not isinstance(data, dict):
                return {
                    "action": "REPLY",
                    "content": f"决策格式错误: AI 返回了非字典类型: {type(data)}",
                }

            return data
        except Exception as e:
            logger.error(
                f"Thinking failed: {e}. Raw response: {response.text if 'response' in locals() else 'N/A'}"
            )
            return {"action": "REPLY", "content": f"决策系统故障: {e}"}

    async def _run_script(self, skill_name, skill_dir, params, ctx):
        """Legacy/Standard execute.py runner"""
        import os
        import sys
        import importlib.util

        execute_script = os.path.join(skill_dir, "scripts", "execute.py")
        if not os.path.exists(execute_script):
            yield f"❌ 找不到脚本: {execute_script}", None, None
            return

        try:
            spec = importlib.util.spec_from_file_location(
                f"{skill_name}_execute", execute_script
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"{skill_name}_execute"] = module
            spec.loader.exec_module(module)

            if not hasattr(module, "execute"):
                yield "❌ 脚本缺少 execute 函数", None, None
                return

            # Execute
            if asyncio.iscoroutinefunction(module.execute):
                result = await module.execute(ctx, params)
            else:
                result = module.execute(ctx, params)

            if isinstance(result, str):
                yield result, None, None
            else:
                yield f"✅ 执行结果: {result}", None, None

        except Exception as e:
            logger.error(f"Script execution error: {e}")
            yield f"❌ 执行出错: {e}", None, None


# Singleton
skill_agent = SkillAgent()
