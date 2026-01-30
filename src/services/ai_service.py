import logging
import time
from core.config import gemini_client, GEMINI_MODEL
from google.genai import types

logger = logging.getLogger(__name__)

class AiService:
    """
    Service for interacting with Gemini AI, acting as a generic Agent Engine.
    Handles:
    - Text generation
    - Tool use (Function Calling) loop
    - Streaming responses
    """

    async def generate_response_stream(
        self, 
        message_history: list, 
        tools: list = None, 
        tool_executor: callable = None,
        system_instruction: str = None
    ):
        """
        Generator for streaming responses with support for Function Calling (Agent Loop).
        
        Args:
            message_history: List of Gemini content objects/dicts.
            tools: List of FunctionDeclaration objects.
            tool_executor: Async callable (name, args) -> result.
            system_instruction: System prompt.
            
        Yields:
            str: Text chunks of the final response.
        """
        MAX_TURNS = 10  # Agent can think for 10 turns
        turn_count = 0 
        
        # Clone history to avoid mutating the original reference too early
        current_history = message_history.copy()
        
        try:
            while turn_count < MAX_TURNS:
                turn_count += 1
                
                config = {"system_instruction": system_instruction}
                if tools:
                    config["tools"] = [types.Tool(function_declarations=tools)]

                if tools:
                    # Non-stream mode required to capture Function Calls reliably?
                    # Gemini API supports streaming function calls, but the SDK handling might be trickier.
                    # For stability, we use non-stream for the decision phase.
                    
                    response = gemini_client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=current_history,
                        config=config,
                    )
                    
                    # 1. Check for Function Calls
                    function_calls = []
                    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                        for part in response.candidates[0].content.parts:
                            if part.function_call:
                                function_calls.append(part.function_call)
                    
                    if function_calls:
                        # Agent decided to act
                        logger.info(f"[AiService] Agent decided to call: {[fc.name for fc in function_calls]}")
                        
                        # Add model's decision to history
                        current_history.append(response.candidates[0].content)
                        
                        # Execute tools
                        for fc in function_calls:
                            if tool_executor:
                                # Yield status update if possible? 
                                # But we can't yield text that isn't part of final response easily without confusing UI?
                                # We can yield a special marker or just be silent.
                                # Let's be silent for now, relying on Telegram's "typing" action from handler.
                                
                                try:
                                    logger.info(f"Executing tool: {fc.name} args={fc.args}")
                                    import asyncio
                                    # Add 60s timeout for tool execution
                                    tool_result = await asyncio.wait_for(tool_executor(fc.name, fc.args), timeout=60.0)
                                except asyncio.TimeoutError:
                                    logger.error(f"Tool execution timed out: {fc.name}")
                                    tool_result = f"Error: Tool '{fc.name}' timed out after 60 seconds."
                                except Exception as e:
                                    logger.error(f"Tool execution error: {e}")
                                    tool_result = f"Error executing tool {fc.name}: {str(e)}"
                                
                                # Add tool result to history
                                # Add tool result to history
                                current_history.append(types.Content(
                                    role="tool",
                                    parts=[types.Part(
                                        function_response=types.FunctionResponse(
                                            name=fc.name,
                                            response={"result": tool_result}
                                        )
                                    )]
                                ))
                            else:
                                logger.error("No tool_executor provided!")
                                break
                                
                        # Continue to next turn (ReAct loop)
                        continue
                        
                    else:
                        # Agent decided to reply with text (Final Answer)
                        try:
                            if response.text:
                                yield response.text
                            else:
                                logger.warning(f"[AiService] Empty text response. Candidates: {response.candidates}")
                                yield "⚠️ 抱歉，模型返回了空响应，可能是触发了安全过滤或内部错误。"
                        except ValueError:
                             # response.text might raise ValueError if there are no text parts
                             logger.warning(f"[AiService] Invalid text response. Candidates: {response.candidates}")
                             yield "⚠️ 抱歉，模型返回了无效响应。"
                        break
                        
                else:
                    # No tools -> Pure streaming chat
                    stream = gemini_client.models.generate_content_stream(
                        model=GEMINI_MODEL,
                        contents=current_history,
                        config=config,
                    )
                    for chunk in stream:
                        if chunk.text:
                            yield chunk.text
                    break

        except Exception as e:
            logger.error(f"[AiService] Error: {e}")
            raise e
