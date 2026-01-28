
import logging
import json
from telegram import Update
from telegram.ext import ContextTypes

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
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE, 
        message_history: list
    ):
        """
        Main entry point for handling user messages via the Agent.
        Returns a generator of text chunks (streaming response).
        """
        user_id = update.effective_user.id
        
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
                    instruction_preview = args["instruction"][:50] + "..." if len(args["instruction"]) > 50 else args["instruction"]
                    await update.message.reply_text(f"🔧 正在调用技能: `{skill_name}`\n📝 指令: {instruction_preview}", parse_mode="Markdown")
                    
                    full_output = ""
                    # Pass update and context for legacy skills
                    async for chunk, files in skill_executor.execute_skill(
                        args["skill_name"], 
                        args["instruction"],
                        update=update,
                        context=context
                    ):
                        full_output += chunk
                        if files:
                             for filename, content in files.items():
                                import io
                                file_obj = io.BytesIO(content)
                                file_obj.name = filename
                                await update.message.reply_document(document=file_obj, filename=filename)
                    
                    if not full_output.strip():
                        logger.warning(f"Skill {skill_name} returned empty output!")
                        return None
                    
                    logger.info(f"Skill {skill_name} output length: {len(full_output)}")
                    logger.info(f"Skill output preview: {full_output[:200]}")
                    
                    return f"Skill Execution Output:\n{full_output}"

                elif name == "search_skill":
                    from services.skill_registry_service import skill_registry
                    
                    status_msg = await update.message.reply_text(f"🔍 Searching for skills: '{args['query']}'...")
                    skills = await skill_registry.search_skills(args["query"])
                    
                    if not skills:
                        return "No matching skills found."
                    
                    results = []
                    for i, s in enumerate(skills[:3]):
                        results.append(f"{i+1}. **{s['name']}** (`{s['repo']}`)\n   {s['description'][:200]}...")
                    
                    response_text = "Found the following skills:\n\n" + "\n\n".join(results) + "\n\nTo install, reply: `Install <Skill Name>` or I can call `install_skill` if you confirm."
                    return response_text

                elif name == "install_skill":
                    from services.skill_registry_service import skill_registry
                    from core.skill_loader import skill_loader
                    
                    skill_name = args["skill_name"]
                    repo_name = args["repo_name"]
                    
                    status_msg = await update.message.reply_text(f"⬇️ Installing skill: {skill_name} from {repo_name}...")
                    
                    success = await skill_registry.install_skill(repo_name, skill_name)
                    if success:
                        skill_loader.scan_skills()
                        return f"Success: Installed skill '{skill_name}'. It is now ready to use."
                    else:
                        return f"Error: Failed to install skill '{skill_name}'."

                elif name == "modify_skill":
                    from services.skill_creator import update_skill
                    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                    from utils import smart_edit_text
                    
                    skill_name = args["skill_name"]
                    instruction = args["instruction"]
                    
                    status_msg = await update.message.reply_text(f"✍️ Generating modification for `{skill_name}`...")
                    
                    result = await update_skill(skill_name, instruction, user_id)
                    
                    if not result["success"]:
                         return f"Error updating skill: {result.get('error', 'Unknown error')}"
                    
                    code = result["code"]
                    filepath = result["filepath"]
                    
                    # Store for callback reference if needed (though callback relies on file existence in pending)
                    context.user_data["pending_skill"] = skill_name
                    
                    code_preview = code[:500] + "..." if len(code) > 500 else code
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("✅ 启用修改", callback_data=f"skill_approve_{skill_name}"),
                            InlineKeyboardButton("❌ 放弃", callback_data=f"skill_reject_{skill_name}")
                        ],
                        [InlineKeyboardButton("📝 查看完整代码", callback_data=f"skill_view_{skill_name}")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    # Send UI (Agent will see execution success, User sees buttons)
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=(
                            f"📝 **Skill 修改草稿**\n\n"
                            f"**Target**: `{skill_name}`\n"
                            f"**Instruction**: {instruction}\n\n"
                            f"```python\n{code_preview}\n```\n\n"
                            f"Please approve to apply changes."
                        ),
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
                    
                    return f"Success: Generated modification for '{skill_name}'. User review required."

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
