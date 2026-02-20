"""
Skill Agent - 智能执行代理
"""

import os

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


class SkillDecision:
    """Decision made by the Agent"""

    def __init__(
        self,
        action: str,
        content: Any = None,
        execute_type: str = None,
        target_skill: str = None,
        instruction: str = None,
    ):
        self.action = action
        self.content = content
        self.execute_type = execute_type
        self.target_skill = target_skill
        self.instruction = instruction

    def __eq__(self, other):
        if not isinstance(other, SkillDecision):
            return False
        return (
            self.action == other.action
            and self.content == other.content
            and self.execute_type == other.execute_type
            and self.target_skill == other.target_skill
            and self.instruction == other.instruction
        )

    def __str__(self):
        return f"[Decision: {self.action} {self.execute_type or ''} {self.target_skill or ''}]"


class SkillFinalReply:
    """Final Reply Object - marks that the skill has completed all steps"""

    def __init__(self, content: str):
        self.content = content

    def __str__(self):
        return f"[Final Reply: {self.content[:50]}...]"


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

        # 替换 skill_content 中的非敏感环境变量为实际值
        for key, value in os.environ.items():
            if key.startswith("X_"):
                # 支持 ${X_VAR} 格式
                skill_content = skill_content.replace(f"${{{key}}}", value)
                # 支持 $X_VAR 格式
                skill_content = skill_content.replace(f"${key}", value)

        logger.debug(f"skill_content: {skill_content}")
        # 2. Think (Decision Making)
        yield f"🧠 SkillAgent ({skill_name}) 正在思考...", None, None

        decision = await self._think(
            skill_name, skill_content, user_request, extra_context
        )

        action = decision.get("action")
        logger.info(f"SkillAgent Decision: {action} - {decision}")

        # Yield Decision Object for loop detection
        skill_decision = SkillDecision(
            action=action,
            content=decision.get("content"),
            execute_type=decision.get("execute_type"),
            target_skill=decision.get("target_skill"),
            instruction=decision.get("instruction"),
        )
        yield "", None, skill_decision

        # 3. Act based on decision
        if action == "REPLY":
            content = decision.get("content", "")
            # 使用 SkillFinalReply 标识这是最终回复
            yield content, None, SkillFinalReply(content)
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
                # 增强可见性：显示脚本参数
                yield f"📜 正在运行脚本，参数: `{content}`", None, None

                async for msg, files, result_obj in self._run_script(
                    skill_name, skill_dir, content, ctx
                ):
                    yield msg, files, result_obj

            elif execute_type == "COMMAND":
                # Run shell command directly
                # 增强可见性：显示具体命令
                yield f"⚙️ 我正在执行 Shell 命令: `{content}`", None, None

                success, output = await sandbox_executor.execute_shell_command(
                    command=content, skill_dir=skill_dir
                )

                if output.strip():
                    yield f"📋 执行输出:\n```\n{output}\n```", None, None
                else:
                    yield "✅ 执行完成 (无输出)。", None, None

                # Return structured result with 🔇🔇🔇 prefix to signal task completion
                status = "成功" if success else "失败"
                result_text = f"🔇🔇🔇✅ Shell 命令执行{status}。" + (
                    f" 输出: {output[:200]}" if output.strip() else ""
                )
                yield (
                    result_text,
                    None,
                    {"text": result_text, "ui": {}, "success": success},
                )

            elif execute_type == "CODE":
                # Run generated python code
                yield "⚙️ 我正在执行代码 (CODE)...", None, None

                success, output, output_files = await sandbox_executor.execute_code(
                    code=content, input_files=input_files, skill_dir=skill_dir
                )

                # Build structured result for agent feedback
                file_names = list(output_files.keys()) if output_files else []
                result_summary = []

                if output_files:
                    result_summary.append(
                        f"生成了 {len(output_files)} 个文件: {', '.join(file_names)}"
                    )
                    # Send files to user
                    yield (
                        f"✅ 执行完成，生成 {len(output_files)} 个文件。",
                        output_files,
                        None,
                    )

                if output.strip():
                    result_summary.append(f"输出:\n{output[:500]}")
                    yield f"📋 执行输出:\n```\n{output}\n```", None, None

                # Return structured result with 🔇🔇🔇 prefix to signal task completion
                status = "成功" if success else "失败"
                result_text = f"🔇🔇🔇✅ 代码执行{status}。" + " ".join(result_summary)
                yield (
                    result_text,
                    None,
                    {
                        "text": result_text,
                        "ui": {},
                        "success": success,
                        "files": file_names,
                    },
                )

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
        logger.debug(f"SkillAgent Decision Prompt: {prompt}")

        try:
            response = await gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            text = response.text
            logger.info(f"SkillAgent Decision Response: {text}")
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
            import inspect

            if inspect.isasyncgenfunction(module.execute):
                async for chunk in module.execute(ctx, params):
                    if isinstance(chunk, str):
                        yield chunk, None, None
                    elif isinstance(chunk, dict) and (
                        "text" in chunk or "ui" in chunk or "files" in chunk
                    ):
                        yield chunk.get("text", ""), chunk.get("files"), chunk
                    else:
                        yield f"{chunk}", None, None
                return

            if asyncio.iscoroutinefunction(module.execute):
                result = await module.execute(ctx, params)
            else:
                result = module.execute(ctx, params)

            # logger.info(f"Skill {skill_name} output: {result}")
            if isinstance(result, str):
                yield result, None, None
            elif isinstance(result, dict) and (
                "text" in result or "ui" in result or "files" in result
            ):
                # Structured result (text + ui + files)
                yield result.get("text", ""), result.get("files"), result
            else:
                yield f"✅ 执行结果: {result}", None, None

        except Exception as e:
            logger.error(f"Script execution error: {e}")
            yield f"❌ 执行出错: {e}", None, None


# Singleton
skill_agent = SkillAgent()
