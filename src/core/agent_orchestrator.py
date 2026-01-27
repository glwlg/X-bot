
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
                if name == "download_video":
                    from services.download_service import download_video
                    status_msg = await update.message.reply_text("⏳ Agent: Starting download...")
                    
                    result = await download_video(args["url"], update.effective_chat.id, status_msg, audio_only=args.get("audio_only", False))
                    
                    if result.success:
                        if args.get("audio_only", False):
                            await context.bot.send_audio(update.effective_chat.id, open(result.file_path, "rb"))
                        else:
                            await context.bot.send_video(update.effective_chat.id, open(result.file_path, "rb"), supports_streaming=True)
                        
                        return f"Success: Video downloaded and sent to user."
                    else:
                        return f"Error: Download failed. {result.error_message}"

                elif name == "set_reminder":
                    from repositories import add_reminder_task
                    from utils import parse_time_input
                    
                    seconds = parse_time_input(args["time_expression"])
                    if not seconds:
                        return "Error: Invalid time format."
                    
                    await add_reminder_task(
                        user_id, update.effective_chat.id, args["content"], seconds, update.message.message_id
                    )
                    return f"Success: Reminder set for '{args['content']}' in {args['time_expression']}."

                elif name == "rss_subscribe":
                    from repositories import add_subscription
                    from services.web_summary_service import fetch_page_title
                    title = await fetch_page_title(args["url"]) or "New Feed"
                    await add_subscription(user_id, args["url"], title)
                    return f"Success: Subscribed to RSS feed: {title}"

                elif name == "monitor_keyword":
                    from repositories import add_subscription
                    
                    keyword = args["keyword"]
                    from urllib.parse import quote
                    rss_url = f"https://news.google.com/rss/search?q={quote(keyword)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
                    title = f"监控: {keyword}"
                    
                    await add_subscription(user_id, rss_url, title)
                    return f"Success: Now monitoring keyword '{keyword}' via Google News."

                elif name == "call_skill":
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

                elif name == "list_subscriptions":
                    from repositories.subscription_repo import get_user_subscriptions
                    from repositories.watchlist_repo import get_user_watchlist
                    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                    from utils import smart_reply_text
                    
                    # user_id is already available from outer scope
                    
                    rss_subs = await get_user_subscriptions(user_id)
                    stocks = await get_user_watchlist(user_id)
                    
                    if not rss_subs and not stocks:
                        return "用户当前没有任何订阅。"

                    text_lines = ["📋 **您的订阅列表 (支持点击删除)**\n"]
                    keyboard = []
                    
                    if rss_subs:
                        text_lines.append(f"\n📢 **RSS 订阅 ({len(rss_subs)})**")
                        temp_row = []
                        for sub in rss_subs:
                            text_lines.append(f"- [{sub['title']}]({sub['feed_url']})")
                            short_title = sub['title'][:8] + ".." if len(sub['title']) > 8 else sub['title']
                            btn = InlineKeyboardButton(f"❌ {short_title}", callback_data=f"del_rss_{sub['id']}")
                            
                            temp_row.append(btn)
                            if len(temp_row) == 2:
                                keyboard.append(temp_row)
                                temp_row = []
                        if temp_row:
                            keyboard.append(temp_row)
                            
                    if stocks:
                        text_lines.append(f"\n📈 **自选股 ({len(stocks)})**")
                        temp_row = []
                        for s in stocks:
                            text_lines.append(f"- {s['stock_name']} (`{s['stock_code']}`)")
                            
                            short_name = s['stock_name'][:8] + ".." if len(s['stock_name']) > 8 else s['stock_name']
                            btn = InlineKeyboardButton(f"❌ {short_name}", callback_data=f"del_stock_{s['stock_code']}")
                            
                            temp_row.append(btn)
                            if len(temp_row) == 2:
                                keyboard.append(temp_row)
                                temp_row = []
                        if temp_row:
                            keyboard.append(temp_row)
                            
                    final_text = "\n".join(text_lines)
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await smart_reply_text(update, final_text, reply_markup=reply_markup)
                    
                    return "[System Hint] The subscription list has been sent to the user as a separate message with DELETE buttons. Do NOT repeat the list in your response. Just confirm you've shown it."

                elif name == "refresh_rss":
                    from handlers.subscription_handlers import refresh_user_subscriptions
                    return await refresh_user_subscriptions(update, context)



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
