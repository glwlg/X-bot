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
        
        # 2. Add Memory Tools (if enabled)
        memory_server = None
        if MCP_MEMORY_ENABLED:
            memory_tools, memory_server = await self._get_memory_tools(user_id)
            if memory_tools:
                # Retrieve function declarations from the nested structure
                # The memory tool helper returns [{'function_declarations': [...]}]
                # We need to flatten it or pass correctly.
                # Gemini SDK expects list of FunctionDeclaration objects in `tools`.
                # Let's see how _get_memory_tools returns it.
                
                # Check implementation below.
                # Actually, typically SDK expects `tools=[Declaration1, Declaration2]`.
                # If _get_memory_tools returns the old structure, we adjust.
                tools.extend(memory_tools)

        # 3. Define Tool Executor (Closure with Context)
        async def tool_executor(name: str, args: dict) -> str:
            logger.info(f"Agent invoking tool: {name} with {args}")
            try:
                # Dispatch to specific handlers
                if name == "download_video":
                    from services.download_service import download_video
                    # Download service typically sends progress messages. 
                    # We might want to pass a dummy message object or handle it gracefully.
                    # For agent, we want it to be silent or minimal until done?
                    # But download tasks are long.
                    # Ideally, we send a "Starting download..." message from here or let the service do it.
                    # The service expects a `message` object to edit.
                    status_msg = await update.message.reply_text("⏳ Agent: Starting download...")
                    
                    # We need to adapt the service to not require a message object if possible, 
                    # or pass this status_msg.
                    result = await download_video(args["url"], update.effective_chat.id, status_msg, audio_only=args.get("audio_only", False))
                    
                    if result.success:
                        # Video is sent by download_service?
                        # Check download_service logic. If it returns file_path, we need to send it.
                        # Wait, download_service usually returns a result object.
                        # We need to handle the sending part if the service doesn't.
                        # Actually, looking at media_handlers, `process_video_download` handles the sending.
                        # `download_video` function ONLY downloads.
                        
                        # So we should reuse `media_handlers` logic if possible, 
                        # OR reimplement sending logic here.
                        # Reusing `process_video_download` is hard because it expects routing flow.
                        # Let's implement basic sending here.
                        
                        # Send file
                        if args.get("audio_only", False):
                            await context.bot.send_audio(update.effective_chat.id, open(result.file_path, "rb"))
                        else:
                            await context.bot.send_video(update.effective_chat.id, open(result.file_path, "rb"), supports_streaming=True)
                        
                        return f"Success: Video downloaded and sent to user."
                    else:
                        return f"Error: Download failed. {result.error_message}"

                elif name == "set_reminder":
                    from services.service_handlers import process_remind
                    # We can reuse process_remind logic if we adapt parameters?
                    # process_remind parses text. Here we have structured args.
                    # Better to call reminder_repo directly.
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
                    # We need to fetch title?
                    from services.web_summary_service import fetch_page_title
                    title = await fetch_page_title(args["url"]) or "New Feed"
                    await add_subscription(user_id, args["url"], title)
                    return f"Success: Subscribed to RSS feed: {title}"

                elif name == "monitor_keyword":
                    from repositories import add_subscription
                    
                    keyword = args["keyword"]
                    # Use Google News RSS for keyword monitoring
                    from urllib.parse import quote
                    rss_url = f"https://news.google.com/rss/search?q={quote(keyword)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
                    title = f"📢 监控: {keyword}"
                    
                    await add_subscription(user_id, rss_url, title)
                    return f"Success: Now monitoring keyword '{keyword}' via Google News."

                elif name == "call_skill":
                    from services.skill_executor import skill_executor
                    # Execute skill
                    # This yields chunks! But tool_executor expects a return string.
                    # We need to consume the skill output and return a summary?
                    # OR, we allow the skill to interact with the user via `update` 
                    # and return a "Task Completed" message to Gemini.
                    
                    full_output = ""
                    async for chunk, files in skill_executor.execute_skill(args["skill_name"], args["instruction"]):
                        # Send intermediate updates to user?
                        # Maybe too noisy. 
                        # Only send if it's significant?
                        full_output += chunk
                        if files:
                             for filename, content in files.items():
                                import io
                                file_obj = io.BytesIO(content)
                                file_obj.name = filename
                                await update.message.reply_document(document=file_obj, filename=filename)
                    
                    return f"Skill Execution Output:\n{full_output}"

                elif name == "search_and_install_skill":
                    from services.skill_registry_service import skill_registry
                    from core.skill_loader import skill_loader
                    
                    status_msg = await update.message.reply_text(f"🔍 Searching for skill: {args['query']}...")
                    skills = await skill_registry.search_skills(args["query"])
                    
                    if not skills:
                        return "Error: No matching skills found in marketplace."
                        
                    # Auto install first match (Fail-Fast)
                    best_match = skills[0]
                    await context.bot.edit_message_text(f"⬇️ Installing: {best_match['name']}...", chat_id=update.effective_chat.id, message_id=status_msg.message_id)
                    
                    success = await skill_registry.install_skill(best_match["repo"], best_match["name"])
                    if success:
                        skill_loader.scan_skills()
                        return f"Success: Installed skill '{best_match['name']}'. You can now call it using `call_skill`."
                    else:
                        return "Error: Installation failed."

                elif name == "list_subscriptions":
                    from repositories.subscription_repo import get_user_subscriptions
                    from repositories.watchlist_repo import get_user_watchlist
                    
                    sub_type = args.get("type", "all")
                    result_lines = []
                    
                    if sub_type in ["rss", "all"]:
                        rss_subs = await get_user_subscriptions(user_id)
                        if rss_subs:
                            result_lines.append(f"📢 **RSS Subscriptions** ({len(rss_subs)}):")
                            for sub in rss_subs:
                                result_lines.append(f"- [{sub['title']}]({sub['feed_url']})")
                        else:
                            if sub_type == "rss":
                                result_lines.append("📢 No RSS subscriptions found.")

                    if sub_type in ["stock", "all"]:
                        stocks = await get_user_watchlist(user_id)
                        if stocks:
                            result_lines.append(f"\n📈 **Stock Watchlist** ({len(stocks)}):")
                            for s in stocks:
                                result_lines.append(f"- {s['stock_name']} (`{s['stock_code']}`)")
                        else:
                             if sub_type == "stock":
                                result_lines.append("📈 No stocks in watchlist.")
                    
                    if not result_lines:
                         return "No subscriptions found."
                    
                    return "\n".join(result_lines)

                # Memory Tools
                elif memory_server:
                     # Dispatch undefined tools to memory server if available
                     return await memory_server.call_tool(name, args)
                
                else:
                    return f"Error: Unknown tool '{name}'"

            except Exception as e:
                logger.error(f"Error in tool_executor: {e}", exc_info=True)
                return f"System Error: {str(e)}"

        # 4. Generate Response
        system_instruction = DEFAULT_SYSTEM_PROMPT
        if memory_server:
            system_instruction = MEMORY_MANAGEMENT_GUIDE

        async for chunk in self.ai_service.generate_response_stream(
            message_history, 
            tools=tools, 
            tool_executor=tool_executor,
            system_instruction=system_instruction
        ):
            yield chunk

    async def _get_memory_tools(self, user_id: int):
        """Helper to get memory tools declarations and server instance"""
        try:
            from mcp_client import mcp_manager
            from mcp_client.tools_bridge import convert_mcp_tools_to_gemini
            from mcp_client.memory import register_memory_server

            register_memory_server()
            memory_server = await mcp_manager.get_server("memory", user_id=user_id)

            if memory_server and memory_server.session:
                mcp_tools_result = await memory_server.session.list_tools()
                # convert_mcp_tools_to_gemini returns a list of FunctionDeclaration
                gemini_funcs = convert_mcp_tools_to_gemini(mcp_tools_result.tools)
                return gemini_funcs, memory_server
        except Exception:
            pass
        return None, None

agent_orchestrator = AgentOrchestrator()
