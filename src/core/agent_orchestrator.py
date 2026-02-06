import logging
import asyncio
from core.platform.models import UnifiedContext

from core.config import MCP_MEMORY_ENABLED
from core.tool_registry import tool_registry
from services.ai_service import AiService
from core.prompts import DEFAULT_SYSTEM_PROMPT, MEMORY_MANAGEMENT_GUIDE

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    The Agent Brain.
    Orchestrates the interaction between:
    1. Tool Registry (Capabilities)
    2. User Context (Telegram Update)
    3. AI Service (Gemini Agent Engine)
    """

    def __init__(self):
        self.ai_service = AiService()
        self._memory_tools_cache = None  # Cache for tool definitions

    async def handle_message(self, ctx: UnifiedContext, message_history: list):
        """
        Main entry point for handling user messages via the Agent.
        Returns a generator of text chunks (streaming response).
        """
        user_id = ctx.message.user.id  # Assuming ID is int compatible for now

        # 0. Dynamic Skill Search (Context Loading)
        # Instead of giving the AI all skills or a generic search tool, we pre-search based on user input.
        # This acts as a "RAG" for tools/skills.
        from core.skill_loader import skill_loader

        # Extract user text from history (last user message)
        last_user_text = ""
        for msg in reversed(message_history):
            # Compatible handle for dict (legacy) or Content object (google.genai.types)
            if isinstance(msg, dict):
                role = msg.get("role")
                parts = msg.get("parts", [])
            else:
                role = getattr(msg, "role", None)
                parts = getattr(msg, "parts", [])

            if role == "user":
                for p in parts:
                    if isinstance(p, dict) and "text" in p:
                        last_user_text = p["text"]
                    elif hasattr(p, "text"):
                        last_user_text = p.text
                break

        # 1. Gather Tools
        # Start with base tools (e.g. skill_manager for explicitly managing skills)
        # Note: We might want a simplified skill_manager tool if we are auto-injecting.
        tools = []

        # Always include skill_manager for explicit "install", "search" etc commands unless handled purely via NLI
        # For now, let's keep the generic capability logic but prioritizing matched skills.

        matched_skills = []
        if last_user_text:
            # Use a lower threshold to catch more potential candidates
            # matched_skills = await skill_loader.find_similar_skills(
            #     last_user_text, threshold=0.4
            # )
            pass

        if matched_skills:
            logger.info(
                f"Dynamic Skill Injection: Found {len(matched_skills)} matches for '{last_user_text[:20]}...'"
            )
            # Create specific tools for these skills
            # We need to ask ToolRegistry to generate tools for these specific skills
            specific_tools = tool_registry.get_specific_skill_tools(matched_skills)
            tools.extend(specific_tools)

        # Add the generic 'call_skill' tool as a fallback (but maybe with reduced description to save tokens?)
        # Or if we trust the search, we might not need it?
        # Safety: Keep generic tool but maybe prompt emphasizes using specific ones?
        # Actually, get_all_tools() returns the generic one.
        # Let's add the generic one LAST as fallback.
        tools.extend(tool_registry.get_all_tools())

        # 2. Add Memory Tools
        if MCP_MEMORY_ENABLED:
            memory_tools = await self._get_memory_tool_definitions(user_id)
            if memory_tools:
                tools.extend(memory_tools)

        # 3. Define Tool Executor (Closure with Context)
        async def tool_executor(name: str, args: dict) -> str:
            logger.info(f"Agent invoking tool: {name} with {args}")
            try:
                # Dispatch to specific handlers
                if name == "call_skill" or name.startswith("skill_"):
                    from agents.skill_agent import (
                        skill_agent,
                        SkillDelegationRequest,
                        SkillFinalReply,
                        SkillDecision,
                    )

                    if name == "call_skill":
                        skill_name = args["skill_name"]
                        instruction = args["instruction"]
                    else:
                        # Dynamic tool: skill_rss_subscribe -> rss_subscribe
                        # Remove prefix "skill_"
                        # skill_manager -> skill_manager
                        safe_name = name[6:] if name != "skill_manager" else name

                        from core.skill_loader import skill_loader

                        # 1. Try exact match (e.g. rss_subscribe)
                        skill_name = safe_name

                        if not skill_loader.get_skill(skill_name):
                            # 2. Try hyphenated version (e.g. data_storytelling -> data-storytelling)
                            alt_name = skill_name.replace("_", "-")
                            if skill_loader.get_skill(alt_name):
                                skill_name = alt_name

                        instruction = args["instruction"]

                    # Notify user about skill invocation (ephemeral, not saved)

                    instruction_preview = (
                        instruction[:200] + "..."
                        if len(instruction) > 200
                        else instruction
                    )
                    await ctx.reply(
                        f"⚡ 准备调用 `{skill_name}` 能力，指令：{instruction_preview}"
                    )

                    full_output = ""
                    extra_context = ""

                    # Continuous Observation Loop (ReAct Pattern)
                    # 只有 REPLY 才退出，EXECUTE 和 DELEGATE 都继续循环
                    MAX_DEPTH = 20
                    MAX_ROUND_OUTPUT_LEN = 2000  # 每轮结果最大长度
                    MAX_CONTEXT_LEN = 8000  # 总 context 最大长度

                    # 循环检测变量
                    last_iteration_output = None
                    last_decision = None
                    decision_loop_counter = 0
                    loop_counter = 0

                    for depth in range(MAX_DEPTH):
                        delegation = None
                        execution_result = None
                        is_final_reply = False
                        iteration_output = ""
                        current_decision = None

                        logger.info(
                            "=============================1================================="
                        )

                        # Check for cancellation
                        from core.task_manager import task_manager

                        if task_manager.is_cancelled(user_id):
                            logger.info(
                                f"Task cancelled by user {user_id} during tool execution loop"
                            )
                            raise asyncio.CancelledError()

                        # 包裹异常捕获，确保错误信息能传递给下一轮
                        try:
                            # Execute Skill Agent (Think -> Act)
                            async for (
                                chunk,
                                files,
                                result_obj,
                            ) in skill_agent.execute_skill(
                                skill_name,
                                instruction,
                                extra_context=extra_context,
                                ctx=ctx,
                            ):
                                logger.info(
                                    "=============================2================================="
                                )
                                # Check for cancellation during streaming
                                if task_manager.is_cancelled(user_id):
                                    raise asyncio.CancelledError()

                                # 检测返回类型
                                if isinstance(result_obj, SkillDelegationRequest):
                                    delegation = result_obj
                                elif isinstance(result_obj, SkillDecision):
                                    current_decision = result_obj
                                elif isinstance(result_obj, SkillFinalReply):
                                    # Agent 明确返回了最终回复
                                    is_final_reply = True
                                elif isinstance(result_obj, dict):
                                    if "ui" in result_obj:
                                        if "pending_ui" not in ctx.user_data:
                                            ctx.user_data["pending_ui"] = []
                                        ctx.user_data["pending_ui"].append(
                                            result_obj["ui"]
                                        )
                                    # 捕获执行结果（用于反馈给下一轮）
                                    execution_result = result_obj

                                    if chunk:
                                        # 只在 result_obj 为空（普通文本流）时发送消息
                                        # 如果是 dict (structured result)，会在后续 execution_result 逻辑中统一发送，避免重复
                                        # 避免发送 Agent 的中间思考消息（如 "正在思考..."）
                                        if (
                                            not isinstance(result_obj, dict)
                                            and not chunk.startswith("🧠")
                                            and not chunk.startswith("🔇🔇🔇")
                                            and not is_final_reply
                                        ):
                                            await ctx.reply(chunk)
                                            logger.info(f"[Round {depth + 1}] {chunk}")

                                    iteration_output += chunk + "\n"

                                if files:
                                    for filename, content in files.items():
                                        await ctx.reply_document(
                                            document=content, filename=filename
                                        )
                        except Exception as e:
                            # 捕获技能执行过程中的异常
                            error_msg = f"❌ 执行出错: {str(e)}"
                            logger.error(
                                f"[Round {depth + 1}] Skill execution error: {e}",
                                exc_info=True,
                            )

                            # 将错误信息发送给用户
                            await ctx.reply(error_msg)

                            # 将错误信息加入 iteration_output 和 execution_result
                            iteration_output += error_msg + "\n"
                            execution_result = {"text": error_msg, "error": str(e)}

                        logger.info(
                            "=============================3================================="
                        )
                        full_output += iteration_output

                        # 检查是否是最终回复（Agent 返回 REPLY action）
                        # 如果 iteration_output 不包含特定的中间状态标记，且没有 delegation，
                        # 我们需要更精确地判断是否是 REPLY
                        # 实际上，SkillAgent 在 REPLY 时会直接 yield content 并 return
                        # 而 EXECUTE 时会 yield 执行结果

                        if delegation:
                            # === DELEGATE: 执行委托并继续循环 ===
                            logger.info(
                                f"[Round {depth + 1}] Delegating to {delegation.target_skill}"
                            )
                            await ctx.reply(
                                f"🔄 正在委托给 `{delegation.target_skill}`: {delegation.instruction}"
                            )

                            # Execute Delegated Skill
                            delegated_output = ""
                            try:
                                async for (
                                    d_chunk,
                                    d_files,
                                    d_result,
                                ) in skill_agent.execute_skill(
                                    delegation.target_skill,
                                    delegation.instruction,
                                    ctx=ctx,
                                ):
                                    if d_chunk:
                                        delegated_output += d_chunk + "\n"
                                    if d_files:
                                        for f_name, f_content in d_files.items():
                                            await ctx.reply_document(
                                                document=f_content, filename=f_name
                                            )
                            except Exception as e:
                                # 捕获委托执行过程中的异常
                                error_msg = f"❌ 委托执行出错: {str(e)}"
                                logger.error(
                                    f"[Round {depth + 1}] Delegation error: {e}",
                                    exc_info=True,
                                )
                                await ctx.reply(error_msg)
                                delegated_output = error_msg + "\n"

                            # 智能截断
                            if len(delegated_output) > MAX_ROUND_OUTPUT_LEN:
                                truncated = delegated_output[:MAX_ROUND_OUTPUT_LEN]
                                truncated += f"\n...[已截断，原长度 {len(delegated_output)} 字符]"
                            else:
                                truncated = delegated_output

                            extra_context += f"\n\n【轮次 {depth + 1} 结果 - {delegation.target_skill}】:\n{truncated}"

                        elif execution_result or iteration_output:
                            # === EXECUTE: 把执行结果加入 context 并继续循环 ===
                            logger.info(
                                "=============================4================================="
                            )
                            # 如果有具体的执行结果（如 write_file 返回的 success），加入上下文
                            if execution_result:
                                result_text = str(execution_result)
                                logger.info(
                                    "=============================5================================="
                                )
                                if isinstance(execution_result, dict):
                                    result_text = execution_result.get(
                                        "text", str(execution_result)
                                    )

                                    # [新增] 将执行结果发送给用户（增强可见性）
                                    # 避免发送纯数据对象的字符串表示，只发送有意义的文本
                                    if "text" in execution_result and result_text:
                                        if not result_text.startswith("🔇🔇🔇"):
                                            await ctx.reply(result_text)

                                if len(result_text) > MAX_ROUND_OUTPUT_LEN:
                                    result_text = (
                                        result_text[:MAX_ROUND_OUTPUT_LEN]
                                        + "...[已截断]"
                                    )

                                command_info = ""
                                if current_decision:
                                    cmd_content = str(current_decision.content)
                                    # Truncate large params in context to save tokens, but keep enough
                                    if len(cmd_content) > 500:
                                        cmd_content = (
                                            cmd_content[:500] + "...[truncated]"
                                        )

                                    command_info = f"【轮次 {depth + 1} 操作】: {current_decision.action}"
                                    if current_decision.execute_type:
                                        command_info += (
                                            f" ({current_decision.execute_type})"
                                        )
                                    command_info += f"\n参数: {cmd_content}\n"

                                extra_context += f"\n\n{command_info}【轮次 {depth + 1} 执行结果】:\n{result_text}"
                                logger.info(
                                    f"[Round {depth + 1}] EXECUTE result captured, continuing..."
                                )
                                logger.info(f"Extra context: {extra_context}")
                                continue

                            # 如果只有文本输出且不是最终回复（例如 Agent 的思考过程）
                            elif not is_final_reply and iteration_output.strip():
                                # 忽略纯状态消息
                                is_status_msg = any(
                                    marker in iteration_output
                                    for marker in [
                                        "正在执行",
                                        "正在思考",
                                        "⚙️",
                                        "🧠",
                                        "👉 委托给",
                                    ]
                                )
                                if not is_status_msg:
                                    extra_context += f"\n\n【轮次 {depth + 1} 输出】:\n{iteration_output[:MAX_ROUND_OUTPUT_LEN]}"
                                    # 注意：这里不continue，以便进行后续的死循环检测

                        # === 死循环检测 (Loop Circuit Breaker) ===

                        # 1. Decision-based Check (Semantic Loop)
                        if (
                            last_decision
                            and current_decision
                            and current_decision == last_decision
                        ):
                            decision_loop_counter += 1
                            logger.warning(
                                f"[Loop Detector] Detailed Decision repeated: {decision_loop_counter} times"
                            )
                            if decision_loop_counter >= 2:
                                failure_msg = f"\n\n⚠️ **系统保护**: 检测到 Agent 在连续尝试相同的操作 ({decision_loop_counter + 1} 次)，任务已强制终止。"
                                await ctx.reply(failure_msg)
                                full_output += failure_msg
                                is_final_reply = True
                        else:
                            decision_loop_counter = 0

                        last_decision = current_decision

                        # 2. Text-based Check (Output Loop)
                        # 检查当前轮次的输出是否与上一轮完全一致
                        current_output_signature = iteration_output.strip()

                        if (
                            last_iteration_output
                            and current_output_signature == last_iteration_output
                        ):
                            loop_counter += 1
                            logger.warning(
                                f"[Loop Detector] Detected identical output for {loop_counter} rounds."
                            )

                            # 放宽阈值：允许连续 2 次重复（即允许重试 1 次）
                            # 只有当连续第 3 次出现相同输出时（loop_counter=2），才触发熔断
                            if loop_counter >= 2:
                                failure_msg = f"\n\n⚠️ **系统保护**: 检测到 Agent 在连续重试相同的操作 ({loop_counter + 1} 次)，任务已强制终止。"
                                await ctx.reply(failure_msg)
                                full_output += failure_msg
                                is_final_reply = True  # 触发循环退出
                        else:
                            loop_counter = 0

                        last_iteration_output = current_output_signature

                        if is_final_reply:
                            logger.info(
                                f"[Round {depth + 1}] Final REPLY detected, breaking loop"
                            )
                            break

                        # 上下文长度管理
                        if len(extra_context) > MAX_CONTEXT_LEN:
                            keep_len = 6000
                            summary = f"【早期轮次摘要】: 之前已完成 {depth} 轮操作。\n"
                            extra_context = summary + extra_context[-keep_len:]

                        logger.info(
                            f"[Round {depth + 1}] extra_context: {extra_context}"
                        )
                        logger.info(
                            f"[Round {depth + 1}] extra_context 长度: {len(extra_context)}"
                        )

                    if not full_output.strip():
                        logger.warning(f"Skill {skill_name} returned empty output!")
                        return None

                    logger.info(
                        f"Skill {skill_name} completed after {depth + 1} rounds, output length: {len(full_output)}"
                    )
                    return f"Skill Execution Output:\n{full_output}"

                # Memory Tools (Lazy Connect)
                else:
                    # Try to see if it's a memory tool
                    if self._is_memory_tool(name):
                        logger.info(
                            f"Connecting to Memory Server for tool execution: {name}"
                        )
                        memory_server = await self._get_active_memory_server(user_id)
                        if memory_server:
                            return await memory_server.call_tool(name, args)

                    return f"Error: Unknown tool '{name}'"

            except Exception as e:
                logger.error(f"Error in tool_executor: {e}", exc_info=True)
                return f"System Error: {str(e)}"

        # 4. Generate Response
        import datetime

        current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")

        # Inject Skill Awareness - User Feedback Optimization
        # Only inject skill_manager details to save context and encourage dynamic lookup
        skill_mgr = skill_loader.get_skill("skill_manager")
        skill_instruction = ""

        if skill_mgr:
            skill_instruction = (
                f"\n\n【系统核心能力】\n"
                f"你不仅仅是一个聊天机器人，你拥有完整的技能管理系统。\n"
                f"skill_manager：{skill_mgr['description']}\n"
            )
        else:
            logger.warning("Skill Manager not found during prompt generation!")

        system_instruction = DEFAULT_SYSTEM_PROMPT
        system_instruction += skill_instruction
        system_instruction += "在你使用call_skill时，你不需要了解skill的详细信息，直接使用自然语言发送指令即可，SkillAgent会处理后续的调用。"
        # system_instruction += "\n⚠️ **提示**：系统可能安装了其他数百个技能。如果你需要特定的能力（如绘制图表、Docker管理等），请务必先调用 `skill_manager`来查找，而不是假设自己不能做。"

        if MCP_MEMORY_ENABLED:
            # Use memory guide if enabled, but we avoid eager connection
            system_instruction += "\n\n" + MEMORY_MANAGEMENT_GUIDE

        # Append dynamic time context
        system_instruction += f"\n\n【当前系统时间】: {current_time_str}"

        async for chunk in self.ai_service.generate_response_stream(
            message_history,
            tools=tools,
            tool_executor=tool_executor,
            system_instruction=system_instruction,
        ):
            yield chunk

    async def _get_memory_tool_definitions(self, user_id: int):
        """
        Get memory tool definitions (schemas).
        Uses caching to avoid connecting on every request.
        """
        if self._memory_tools_cache:
            return self._memory_tools_cache

        try:
            # First time: Need to connect and fetch
            logger.info("Fetching Memory Tool Definitions (One-time init)...")
            from mcp_client import mcp_manager
            from mcp_client.tools_bridge import convert_mcp_tools_to_gemini
            from mcp_client.memory import register_memory_server

            register_memory_server()
            # We start server just to get tools, then we can let it be (manager handles process)
            memory_server = await mcp_manager.get_server("memory", user_id=user_id)

            if memory_server and memory_server.session:
                mcp_tools_result = await memory_server.session.list_tools()
                gemini_funcs = convert_mcp_tools_to_gemini(mcp_tools_result.tools)

                self._memory_tools_cache = gemini_funcs
                return gemini_funcs
        except Exception as e:
            logger.error(f"Failed to fetch memory tools: {e}")
            pass
        return None

    async def _get_active_memory_server(self, user_id: int):
        """
        Get an active connection to the memory server for EXECUTION.
        """
        try:
            from mcp_client import mcp_manager
            from mcp_client.memory import register_memory_server

            register_memory_server()
            return await mcp_manager.get_server("memory", user_id=user_id)
        except Exception:
            return None

    def _is_memory_tool(self, name: str) -> bool:
        """Check if tool name belongs to memory tools"""
        if not self._memory_tools_cache:
            return False
        # Check against cached definitions
        for tool in self._memory_tools_cache:
            if tool.get("name") == name:
                return True
        return False


agent_orchestrator = AgentOrchestrator()
