import logging
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
        user_id = int(ctx.message.user.id)  # Assuming ID is int compatible for now

        # 1. Gather Tools
        tools = tool_registry.get_all_tools()

        # 2. Add Memory Tools (Lazy Load Definitions)
        if MCP_MEMORY_ENABLED:
            memory_tools = await self._get_memory_tool_definitions(user_id)
            if memory_tools:
                tools.extend(memory_tools)

        # 3. Define Tool Executor (Closure with Context)
        async def tool_executor(name: str, args: dict) -> str:
            logger.info(f"Agent invoking tool: {name} with {args}")
            try:
                # Dispatch to specific handlers
                # Dispatch to specific handlers
                if name == "call_skill":
                    from agents.skill_agent import skill_agent, SkillDelegationRequest

                    # Notify user about skill invocation (ephemeral, not saved)
                    skill_name = args["skill_name"]
                    instruction = args["instruction"]

                    instruction_preview = (
                        instruction[:200] + "..."
                        if len(instruction) > 200
                        else instruction
                    )
                    await ctx.reply(
                        f"🔧 正在调用技能: `{skill_name}`\n📝 指令: `{instruction_preview}`"
                    )

                    full_output = ""
                    extra_context = ""
                    current_skill = skill_name
                    current_instruction = instruction

                    # Delegation Loop (Max depth 3 to prevent infinite loops)
                    MAX_DEPTH = 3

                    for depth in range(MAX_DEPTH):
                        delegation = None
                        iteration_output = ""

                        # Execute Skill
                        async for chunk, files, result_obj in skill_agent.execute_skill(
                            current_skill,
                            current_instruction,
                            extra_context=extra_context,
                            ctx=ctx,
                        ):
                            if chunk:
                                await ctx.reply(chunk)
                                logger.info(chunk)
                                iteration_output += chunk + "\n"
                                # Stream crucial updates to user? Or maybe just status.
                                # SkillAgent yields status messages.
                                if (
                                    "正在" in chunk
                                    or "完成" in chunk
                                    or "委托" in chunk
                                ):
                                    pass  # Let Agent allow these?
                                    # Actually AgentOrchestrator usually waits for generator.
                                    # If we want to stream status, we can.

                            if files:
                                for filename, content in files.items():
                                    await ctx.reply_document(
                                        document=content, filename=filename
                                    )

                            if isinstance(result_obj, SkillDelegationRequest):
                                delegation = result_obj

                        full_output += iteration_output

                        if delegation:
                            logger.info(f"Delegating to {delegation.target_skill}")
                            await ctx.reply(
                                f"🔄 (层级 {depth + 1}) 正在委托给 `{delegation.target_skill}`: {delegation.instruction}"
                            )

                            # Execute Delegated Skill (Capture output for context)
                            delegated_output = ""
                            async for d_chunk, d_files, _ in skill_agent.execute_skill(
                                delegation.target_skill, delegation.instruction, ctx=ctx
                            ):
                                if d_chunk:
                                    delegated_output += d_chunk
                                if d_files:
                                    # Send files from delegated skill too
                                    for f_name, f_content in d_files.items():
                                        await ctx.reply_document(
                                            document=f_content, filename=f_name
                                        )

                            # Add to context and continue loop (re-execute original skill with new context)
                            extra_context += f"\n\n[来自 {delegation.target_skill} 的结果]:\n{delegated_output}\n"
                            # Continue loop: execute current_skill again (it will now see the context and likely EXECUTE)
                            continue
                        else:
                            # No delegation, we are done
                            break

                    if not full_output.strip():
                        logger.warning(f"Skill {skill_name} returned empty output!")
                        return None

                    logger.info(f"Skill {skill_name} output length: {len(full_output)}")

                    return f"Skill Execution Output:\n{full_output}"

                # elif name == "evolve_capability":
                #     # REMOVED: Evolution is now handled via skill_manager
                #     pass

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
        from core.skill_loader import skill_loader

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
        system_instruction += "\n⚠️ **提示**：系统可能安装了其他数百个技能。如果你需要特定的能力（如绘制图表、Docker管理等），请务必先调用 `skill_manager` 的 `search_skills` 或 `list_skills` 来查找，而不是假设自己不能做。"

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
