import logging
import time
from config import gemini_client, GEMINI_MODEL, MCP_MEMORY_ENABLED

logger = logging.getLogger(__name__)

class AiService:
    """
    Service for interacting with Gemini AI, handling:
    - Text generation
    - Function calling loop (MCP integration)
    - Tool injection
    """

    async def get_memory_tools(self, user_id: int):
        """
        Retrieves configured memory tools for the specific user.
        """
        if not MCP_MEMORY_ENABLED:
            return None, None

        try:
            from mcp_client import mcp_manager
            from mcp_client.tools_bridge import convert_mcp_tools_to_gemini
            from mcp_client.memory import register_memory_server

            # Ensure Memory Server class is registered
            register_memory_server()

            # Get user-specific memory server instance
            memory_server = await mcp_manager.get_server("memory", user_id=user_id)

            if memory_server and memory_server.session:
                # List tools from the server
                mcp_tools_result = await memory_server.session.list_tools()
                gemini_funcs = convert_mcp_tools_to_gemini(mcp_tools_result.tools)

                if gemini_funcs:
                    tools_config = [{"function_declarations": gemini_funcs}]
                    logger.info(f"[AiService] Injected {len(gemini_funcs)} memory tools for user {user_id}.")
                    return tools_config, memory_server
        
        except Exception as e:
            logger.error(f"[AiService] Failed to setup memory tools: {e}")
        
        return None, None

    async def execute_tool(self, tool_name: str, tool_args: dict, memory_server=None):
        """
        Executes a single tool call through the MCP bridge.
        """
        try:
            # Currently only supports Memory tools via the specific server instance
            if memory_server:
                return await memory_server.call_tool(tool_name, tool_args)
            else:
                # Fallback to manager if server instance not provided (less safe for isolation)
                from mcp_client import mcp_manager
                return await mcp_manager.call_tool("memory", tool_name, tool_args)
        except Exception as e:
            logger.error(f"[AiService] Tool execution failed: {e}")
            return {"error": str(e)}

    async def generate_response(self, user_id: int, context_messages: list, system_instruction: str = None) -> str:
        """
        Generates a response from Gemini, handling potential function calling loops.
        
        Returns:
            str: The final text response from the AI.
        """
        
        # 1. Setup Tools
        tools_config, memory_server = await self.get_memory_tools(user_id)
        
        # Adjust system instruction if tools are present
        if tools_config and not system_instruction:
            from prompts import MEMORY_MANAGEMENT_GUIDE
            system_instruction = MEMORY_MANAGEMENT_GUIDE
        elif not system_instruction:
            from prompts import DEFAULT_SYSTEM_PROMPT
            system_instruction = DEFAULT_SYSTEM_PROMPT

        # 2. Function Calling Loop
        MAX_TURNS = 5
        turn_count = 0
        final_text = ""

        # If we have tools, we start with non-streaming to support function calls
        # If no tools, we can't do function calls, so we could theoretically stream, 
        # but to keep this service simple API-wise, we might just return text 
        # OR implementation a generator.
        
        # To preserve the original behavior (Streaming for simple chat), 
        # we need to decide if we want to expose a generator.
        # For simplicity in refactoring, let's just return the full text first, 
        # OR handle streaming internally if no tools.
        
        # However, the user liked the typing effect. 
        # Providing a generator `generate_response_stream` might be better.
        
        # Let's stick to the Loop logic first.

        message_history = context_messages.copy()

        try:
            while turn_count < MAX_TURNS:
                turn_count += 1
                
                # Determine config
                config = {"system_instruction": system_instruction}
                if tools_config:
                    config["tools"] = tools_config

                # If tools are enabled, use generate_content (non-stream) to catch function calls
                if tools_config:
                    response = gemini_client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=message_history,
                        config=config,
                    )
                    
                    # Check for function calls
                    function_calls = []
                    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                        for part in response.candidates[0].content.parts:
                            if part.function_call:
                                function_calls.append(part.function_call)
                    
                    if function_calls:
                        logger.info(f"[AiService] Function calls requested: {[fc.name for fc in function_calls]}")
                        
                        # Add model response to history
                        message_history.append(response.candidates[0].content)
                        
                        # Execute tools
                        for fc in function_calls:
                            tool_result = await self.execute_tool(fc.name, fc.args, memory_server)
                            
                            # Add tool response to history
                            message_history.append({
                                "role": "tool",
                                "parts": [{
                                    "function_response": {
                                        "name": fc.name,
                                        "response": {"result": tool_result}
                                    }
                                }]
                            })
                        continue # Next turn
                    else:
                        # Final text response
                        final_text = response.text if response.text else ""
                        return final_text

                else:
                    # No tools -> Stream
                    # We can consume the stream here and return full text, 
                    # or yield updates. 
                    # For this refactor, let's just return the full text to keep it simple,
                    # effectively converting stream to block.
                    # Wait, the original code used stream to update UI.
                    # If I make this blocking, the user loses the "Typing..." effect update.
                    
                    # Compromise: This method returns an AsyncIterator if possible?
                    # Or just return the stream object?
                    # But the stream object from Gemini doesn't support the function calling loop logic we built.
                    
                    # Let's keep the stream logic in the handler ONLY for the non-tool case?
                    # No, that defeats the purpose of abstraction.
                    
                    # Let's make this function return a generator that yields text chunks.
                    
                    response_stream = gemini_client.models.generate_content_stream(
                        model=GEMINI_MODEL,
                        contents=message_history,
                        config=config,
                    )
                    
                    collected_text = ""
                    for chunk in response_stream:
                        if chunk.text:
                            collected_text += chunk.text
                    
                    return collected_text

        except Exception as e:
            logger.error(f"[AiService] Generation error: {e}")
            raise e
        
        return final_text
    
    async def generate_response_stream(self, user_id: int, context_messages: list, enable_memory: bool = False):
        """
        Generator for streaming responses.
        Handles function calling internally (non-yielded) until final text is produced.
        """
        # 1. Setup Tools
        tools_config = None
        memory_server = None
        
        if enable_memory:
            tools_config, memory_server = await self.get_memory_tools(user_id)
        
        if tools_config:
            from prompts import MEMORY_MANAGEMENT_GUIDE
            system_instruction = MEMORY_MANAGEMENT_GUIDE
        else:
            from prompts import DEFAULT_SYSTEM_PROMPT
            system_instruction = DEFAULT_SYSTEM_PROMPT

        MAX_TURNS = 5 
        turn_count = 0 
        message_history = context_messages.copy()
        
        try:
            while turn_count < MAX_TURNS:
                turn_count += 1
                config = {"system_instruction": system_instruction}
                if tools_config:
                    config["tools"] = tools_config

                if tools_config:
                    # Non-stream for function calling capability
                    response = gemini_client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=message_history,
                        config=config,
                    )
                    
                    function_calls = []
                    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                        for part in response.candidates[0].content.parts:
                            if part.function_call:
                                function_calls.append(part.function_call)
                    
                    if function_calls:
                        logger.info(f"[AiService] Function calls: {[fc.name for fc in function_calls]}")
                        message_history.append(response.candidates[0].content)
                        
                        for fc in function_calls:
                            # Yield a status update so the user knows tools are running?
                            # yield f"🛠️ 正在执行记忆操作: {fc.name}..." 
                            # But tricky to mix status with final text. 
                            # Let's just log and wait.
                            
                            tool_result = await self.execute_tool(fc.name, fc.args, memory_server)
                            message_history.append({
                                "role": "tool",
                                "parts": [{"function_response": {"name": fc.name, "response": {"result": tool_result}}}]
                            })
                        continue
                    else:
                        # Final text, yield it
                        if response.text:
                            yield response.text
                        break
                else:
                    # Stream mode
                    stream = gemini_client.models.generate_content_stream(
                        model=GEMINI_MODEL,
                        contents=message_history,
                        config=config,
                    )
                    for chunk in stream:
                        if chunk.text:
                            yield chunk.text
                    break

        except Exception as e:
            logger.error(f"[AiService] Error: {e}")
            raise e
