
import logging
import json
from telegram import Update
from telegram.ext import ContextTypes
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

    async def handle_message(
        self, 
        ctx: UnifiedContext, 
        message_history: list
    ):
        """
        Main entry point for handling user messages via the Agent.
        Returns a generator of text chunks (streaming response).
        """
        user_id = int(ctx.message.user.id) # Assuming ID is int compatible for now
        
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
                if name == "call_skill":
                    from services.skill_executor import skill_executor
                    
                    # Notify user about skill invocation (ephemeral, not saved)
                    skill_name = args["skill_name"]
                    instruction_preview = args["instruction"][:100] + "..." if len(args["instruction"]) > 100 else args["instruction"]
                    await ctx.reply(f"🔧 正在调用技能: `{skill_name}`\n📝 指令: `{instruction_preview}`")
                    
                    full_output = ""
                    # Pass unified context
                    async for chunk, files in skill_executor.execute_skill(
                        args["skill_name"], 
                        args["instruction"],
                        ctx=ctx
                    ):
                        full_output += chunk
                        if files:
                             for filename, content in files.items():
                                await ctx.reply_document(document=content, filename=filename)
                    
                    if not full_output.strip():
                        logger.warning(f"Skill {skill_name} returned empty output!")
                        return None
                    
                    logger.info(f"Skill {skill_name} output length: {len(full_output)}")
                    logger.info(f"Skill output preview: {full_output[:200]}")
                    
                    return f"Skill Execution Output:\n{full_output}"

                elif name == "evolve_capability":
                    from core.evolution_router import evolution_router
                    
                    user_request = args.get("user_request", "")
                    await ctx.reply(f"🤔 这个问题... 我现在的技能库里好像还没装对应的功能。\n⚡️ 不过没关系！正在启动自我进化程序，现场为您现学现卖……\n\n🎯 学习目标：{user_request}")
                    
                    # Evolution Router still expects update object for now, pass legacy if available
                    # TODO: Migrate Evolution Router to UnifiedContext
                    result = await evolution_router.evolve(user_request, user_id, ctx.platform_event)
                    return f"Evolution Result:\n{result}"

                # Memory Tools (Lazy Connect)
                else:
                    # Try to see if it's a memory tool
                    if self._is_memory_tool(name):
                         logger.info(f"Connecting to Memory Server for tool execution: {name}")
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
        
        system_instruction = DEFAULT_SYSTEM_PROMPT
        if MCP_MEMORY_ENABLED:
             # Use memory guide if enabled, but we avoid eager connection
            system_instruction = MEMORY_MANAGEMENT_GUIDE
            
        # Append dynamic time context
        system_instruction += f"\n\n【当前系统时间】: {current_time_str}"

        async for chunk in self.ai_service.generate_response_stream(
            message_history, 
            tools=tools, 
            tool_executor=tool_executor,
            system_instruction=system_instruction
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
