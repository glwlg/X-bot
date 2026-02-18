import logging
import inspect
import os
import json
import asyncio
from core.config import gemini_client, GEMINI_MODEL
from google.genai import types

logger = logging.getLogger(__name__)


def _split_text_for_streaming(text: str, max_chars: int) -> list[str]:
    payload = str(text or "")
    if not payload:
        return []
    if len(payload) <= max_chars:
        return [payload]

    chunks: list[str] = []
    remaining = payload
    breakpoints = ["\n\n", "\n", "。", "！", "？", ". ", "! ", "? "]
    min_boundary = int(max_chars * 0.35)

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        head = remaining[:max_chars]
        cut = -1
        for marker in breakpoints:
            idx = head.rfind(marker)
            if idx >= 0:
                candidate = idx + len(marker)
                if candidate > cut:
                    cut = candidate

        if cut < min_boundary:
            cut = max_chars

        chunks.append(remaining[:cut])
        remaining = remaining[cut:]

    return [chunk for chunk in chunks if chunk]


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
        system_instruction: str = None,
        event_callback: callable = None,
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
        try:
            MAX_TURNS = max(1, int(os.getenv("AI_TOOL_MAX_TURNS", "20")))
        except ValueError:
            MAX_TURNS = 20
        try:
            TOOL_EXEC_TIMEOUT_SEC = max(
                30, int(os.getenv("AI_TOOL_EXEC_TIMEOUT_SEC", "420"))
            )
        except ValueError:
            TOOL_EXEC_TIMEOUT_SEC = 420
        tool_final_stream_enabled = (
            os.getenv("AI_TOOL_FINAL_STREAM_ENABLED", "true").lower() == "true"
        )
        try:
            tool_final_stream_chunk_chars = max(
                120,
                int(os.getenv("AI_TOOL_FINAL_STREAM_CHUNK_CHARS", "900")),
            )
        except ValueError:
            tool_final_stream_chunk_chars = 900
        try:
            MAX_REPEAT_TOOL_CALLS = max(2, int(os.getenv("AI_TOOL_REPEAT_GUARD", "3")))
        except ValueError:
            MAX_REPEAT_TOOL_CALLS = 3
        turn_count = 0
        completed = False
        has_tool_call = False
        pending_tool_failures: list[str] = []
        last_tool_signature = ""
        repeat_tool_call_count = 0
        last_terminal_success_text = ""
        last_terminal_success_summary = ""
        last_terminal_tool_name = ""

        # Clone history to avoid mutating the original reference too early
        current_history = message_history.copy()

        try:

            async def _emit(event: str, payload: dict):
                if not event_callback:
                    return None
                try:
                    maybe_coro = event_callback(event, payload)
                    if inspect.isawaitable(maybe_coro):
                        return await maybe_coro
                    return maybe_coro
                except Exception as exc:
                    logger.debug("[AiService] event_callback error: %s", exc)
                    return None

            while turn_count < MAX_TURNS:
                turn_count += 1
                await _emit("turn_start", {"turn": turn_count})

                config = {"system_instruction": system_instruction}
                if tools:
                    config["tools"] = [types.Tool(function_declarations=tools)]

                if tools:
                    # Non-stream mode required to capture Function Calls reliably?
                    # Gemini API supports streaming function calls, but the SDK handling might be trickier.
                    # For stability, we use non-stream for the decision phase.

                    logger.debug(
                        f"🤖 [AiService] Sending prompt to AI (Tools Mode):\n{current_history}"
                    )
                    logger.debug(
                        f"🤖 [AiService] Sending config to AI (Tools Mode):\n{config}"
                    )
                    response = await gemini_client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=current_history,
                        config=config,
                    )

                    # 1. Check for Function Calls
                    function_calls = []
                    if (
                        response.candidates
                        and response.candidates[0].content
                        and response.candidates[0].content.parts
                    ):
                        for part in response.candidates[0].content.parts:
                            if part.function_call:
                                function_calls.append(part.function_call)

                    if function_calls:
                        # Agent decided to act
                        has_tool_call = True
                        signature = self._build_tool_signature(function_calls)
                        if signature == last_tool_signature:
                            repeat_tool_call_count += 1
                        else:
                            last_tool_signature = signature
                            repeat_tool_call_count = 1

                        if repeat_tool_call_count >= MAX_REPEAT_TOOL_CALLS:
                            loop_payload = {
                                "turn": turn_count,
                                "repeat_count": repeat_tool_call_count,
                                "tool_names": [fc.name for fc in function_calls],
                                "signature": signature,
                            }
                            directive = await _emit("loop_guard", loop_payload)
                            forced_reply = (
                                str((directive or {}).get("final_text", "")).strip()
                                if isinstance(directive, dict)
                                else ""
                            )
                            if forced_reply:
                                if tools and tool_final_stream_enabled:
                                    for segment in _split_text_for_streaming(
                                        forced_reply,
                                        tool_final_stream_chunk_chars,
                                    ):
                                        yield segment
                                else:
                                    yield forced_reply
                            elif (
                                last_terminal_success_text
                                or last_terminal_success_summary
                            ):
                                fallback_text = (
                                    last_terminal_success_text
                                    or f"✅ 任务已完成：{last_terminal_success_summary}"
                                )
                                if tools and tool_final_stream_enabled:
                                    for segment in _split_text_for_streaming(
                                        fallback_text,
                                        tool_final_stream_chunk_chars,
                                    ):
                                        yield segment
                                else:
                                    yield fallback_text
                            else:
                                yield (
                                    "⚠️ 检测到重复工具调用，已自动停止以避免死循环。"
                                    "请查看当前结果并按需继续。"
                                )
                            completed = True
                            break

                        logger.info(
                            f"[AiService] Agent decided to call: {[fc.name for fc in function_calls]}"
                        )

                        # Add model's decision to history
                        current_history.append(response.candidates[0].content)

                        # Execute tools
                        turn_failures: list[str] = []
                        terminal_short_circuit_text = ""
                        should_terminal_stop = False
                        for fc in function_calls:
                            if tool_executor:
                                await _emit(
                                    "tool_call_started",
                                    {
                                        "turn": turn_count,
                                        "name": fc.name,
                                        "args": fc.args,
                                    },
                                )
                                # Yield status update if possible?
                                # But we can't yield text that isn't part of final response easily without confusing UI?
                                # We can yield a special marker or just be silent.
                                # Let's be silent for now, relying on Telegram's "typing" action from handler.

                                try:
                                    logger.info(
                                        f"Executing tool: {fc.name} args={fc.args}"
                                    )
                                    # Increase timeout for tool execution (Deep Research can take time)
                                    tool_result = await asyncio.wait_for(
                                        tool_executor(fc.name, fc.args),
                                        timeout=float(TOOL_EXEC_TIMEOUT_SEC),
                                    )
                                except asyncio.TimeoutError:
                                    logger.error(f"Tool execution timed out: {fc.name}")
                                    tool_result = (
                                        f"Error: Tool '{fc.name}' timed out after "
                                        f"{TOOL_EXEC_TIMEOUT_SEC} seconds."
                                    )
                                except Exception as e:
                                    logger.error(f"Tool execution error: {e}")
                                    tool_result = (
                                        f"Error executing tool {fc.name}: {str(e)}"
                                    )

                                tool_ok = self._tool_result_ok(tool_result)
                                task_outcome = ""
                                is_terminal = False
                                terminal_text = ""
                                terminal_ui = {}
                                terminal_payload = {}
                                failure_mode = ""
                                if isinstance(tool_result, dict):
                                    task_outcome = (
                                        str(tool_result.get("task_outcome") or "")
                                        .strip()
                                        .lower()
                                    )
                                    is_terminal = (
                                        bool(tool_result.get("terminal"))
                                        or task_outcome == "done"
                                    )
                                    (
                                        terminal_text,
                                        terminal_ui,
                                        terminal_payload,
                                    ) = self._extract_terminal_artifacts(tool_result)
                                    failure_mode = (
                                        str(tool_result.get("failure_mode") or "")
                                        .strip()
                                        .lower()
                                    )
                                elif tool_result is not None:
                                    terminal_text = str(tool_result).strip()
                                    terminal_payload = {"text": terminal_text}

                                if not failure_mode and not tool_ok:
                                    failure_mode = "recoverable"
                                if failure_mode not in {"recoverable", "fatal"}:
                                    failure_mode = "recoverable" if not tool_ok else ""

                                if is_terminal and tool_ok:
                                    last_terminal_success_text = terminal_text
                                    last_terminal_success_summary = (
                                        self._summarize_tool_result(tool_result)
                                    )
                                    last_terminal_tool_name = fc.name

                                if not tool_ok:
                                    turn_failures.append(
                                        f"{fc.name}: {self._summarize_tool_result(tool_result)}"
                                    )
                                directive = await _emit(
                                    "tool_call_finished",
                                    {
                                        "turn": turn_count,
                                        "name": fc.name,
                                        "ok": tool_ok,
                                        "summary": self._summarize_tool_result(
                                            tool_result
                                        ),
                                        "terminal": is_terminal,
                                        "task_outcome": task_outcome,
                                        # Keep full terminal text so orchestrator can
                                        # deliver complete URLs/commands without truncation.
                                        "terminal_text": terminal_text,
                                        "terminal_text_preview": terminal_text[:200],
                                        "terminal_ui": terminal_ui,
                                        "terminal_payload": terminal_payload,
                                        "failure_mode": failure_mode,
                                    },
                                )

                                # Add tool result to history
                                # Add tool result to history
                                current_history.append(
                                    types.Content(
                                        role="tool",
                                        parts=[
                                            types.Part(
                                                function_response=types.FunctionResponse(
                                                    name=fc.name,
                                                    response={"result": tool_result},
                                                )
                                            )
                                        ],
                                    )
                                )

                                if (
                                    isinstance(directive, dict)
                                    and directive.get("stop") is True
                                ):
                                    terminal_short_circuit_text = str(
                                        directive.get("final_text") or ""
                                    ).strip()
                                    if not terminal_short_circuit_text:
                                        terminal_short_circuit_text = (
                                            terminal_text
                                            or self._summarize_tool_result(tool_result)
                                        )
                                    should_terminal_stop = True
                                    break
                            else:
                                logger.error("No tool_executor provided!")
                                break

                        pending_tool_failures = turn_failures

                        if should_terminal_stop:
                            if tools and tool_final_stream_enabled:
                                for segment in _split_text_for_streaming(
                                    terminal_short_circuit_text,
                                    tool_final_stream_chunk_chars,
                                ):
                                    yield segment
                            else:
                                yield terminal_short_circuit_text
                            completed = True
                            break

                        # Continue to next turn (ReAct loop)
                        continue

                    else:
                        model_text = ""
                        try:
                            model_text = response.text or ""
                        except ValueError:
                            model_text = ""

                        if (
                            has_tool_call
                            and pending_tool_failures
                            and turn_count < MAX_TURNS
                        ):
                            logger.info(
                                "[AiService] Tool failure detected; forcing another attempt. failures=%s",
                                pending_tool_failures,
                            )
                            if response.candidates and response.candidates[0].content:
                                current_history.append(response.candidates[0].content)
                            retry_payload = {
                                "turn": turn_count,
                                "failures": pending_tool_failures[:],
                                "model_text_preview": model_text.replace("\n", " ")[
                                    :160
                                ],
                            }
                            directive = await _emit(
                                "retry_after_failure", retry_payload
                            )
                            recovery_instruction = ""
                            if isinstance(directive, dict):
                                recovery_instruction = str(
                                    directive.get("recovery_instruction") or ""
                                ).strip()
                            if not recovery_instruction:
                                recovery_instruction = (
                                    "系统提示：上一步工具执行失败，任务尚未完成。"
                                    "请优先继续调用可用工具尝试修复并完成交付，"
                                    "不要先向用户提问。失败摘要："
                                    + "; ".join(pending_tool_failures[:3])
                                )
                            current_history.append(
                                types.Content(
                                    role="user",
                                    parts=[types.Part(text=recovery_instruction)],
                                )
                            )
                            continue

                        # Agent decided to reply with text (Final Answer)
                        try:
                            if model_text:
                                preview = model_text.replace("\n", " ")[:200]
                                logger.info(
                                    "[AiService] Model returned final text without tool call (turn=%s): %s",
                                    turn_count,
                                    preview,
                                )
                                await _emit(
                                    "final_response",
                                    {
                                        "turn": turn_count,
                                        "text_preview": preview,
                                    },
                                )
                                if tools and tool_final_stream_enabled:
                                    for segment in _split_text_for_streaming(
                                        model_text,
                                        tool_final_stream_chunk_chars,
                                    ):
                                        yield segment
                                else:
                                    yield model_text
                            else:
                                logger.warning(
                                    f"[AiService] Empty text response. Candidates: {response.candidates}"
                                )
                                await _emit(
                                    "final_response",
                                    {
                                        "turn": turn_count,
                                        "text_preview": "",
                                    },
                                )
                                yield "⚠️ 抱歉，模型返回了空响应，可能是触发了安全过滤或内部错误。"
                        except ValueError:
                            # response.text might raise ValueError if there are no text parts
                            logger.warning(
                                f"[AiService] Invalid text response. Candidates: {response.candidates}"
                            )
                            yield "⚠️ 抱歉，模型返回了无效响应。"
                        completed = True
                        break

                else:
                    # No tools -> Pure streaming chat
                    logger.debug(
                        f"🤖 [AiService] Sending prompt to AI (Stream Mode):\n{current_history}"
                    )
                    stream = await gemini_client.aio.models.generate_content_stream(
                        model=GEMINI_MODEL,
                        contents=current_history,
                        config=config,
                    )
                    async for chunk in stream:
                        if chunk.text:
                            yield chunk.text
                    completed = True
                    break

            if tools and not completed and turn_count >= MAX_TURNS:
                logger.warning(
                    "[AiService] Reached MAX_TURNS (%s) without a final response.",
                    MAX_TURNS,
                )
                await _emit(
                    "max_turn_limit",
                    {
                        "max_turns": MAX_TURNS,
                        "terminal_tool_name": last_terminal_tool_name,
                        "terminal_summary": last_terminal_success_summary,
                        "terminal_text_preview": last_terminal_success_text[:200],
                    },
                )
                if last_terminal_success_text or last_terminal_success_summary:
                    fallback_text = (
                        last_terminal_success_text
                        or f"✅ 任务已完成：{last_terminal_success_summary}"
                    )
                    if tools and tool_final_stream_enabled:
                        for segment in _split_text_for_streaming(
                            fallback_text,
                            tool_final_stream_chunk_chars,
                        ):
                            yield segment
                    else:
                        yield fallback_text
                    return
                yield (
                    f"⚠️ 工具调用轮次已达上限（{MAX_TURNS}），任务仍未完成。"
                    "请把任务拆分为更小步骤后重试。"
                )

        except Exception as e:
            logger.error(f"[AiService] Error: {e}")
            raise e

    @staticmethod
    def _tool_result_ok(tool_result) -> bool:
        if isinstance(tool_result, dict):
            if "ok" in tool_result:
                return bool(tool_result.get("ok"))
            if tool_result.get("success") is False:
                return False
            text = str(tool_result.get("message") or tool_result.get("text") or "")
            lowered = text.lower().strip()
            if lowered.startswith("❌") or lowered.startswith("error"):
                return False
            return True

        if isinstance(tool_result, str):
            lowered = tool_result.lower().strip()
            if lowered.startswith("❌"):
                return False
            if lowered.startswith("error") or "traceback" in lowered:
                return False
            return True

        return tool_result is not None

    @staticmethod
    def _summarize_tool_result(tool_result) -> str:
        if isinstance(tool_result, dict):
            if "text" in tool_result and tool_result["text"]:
                return str(tool_result["text"])[:200]
            if "result" in tool_result and tool_result["result"]:
                return str(tool_result["result"])[:200]
            if "message" in tool_result and tool_result["message"]:
                return str(tool_result["message"])[:200]
            if "summary" in tool_result and tool_result["summary"]:
                return str(tool_result["summary"])[:200]
            return str(tool_result)[:200]
        return str(tool_result)[:200]

    @staticmethod
    def _extract_terminal_artifacts(tool_result) -> tuple[str, dict, dict]:
        text = ""
        ui: dict = {}
        payload: dict = {}
        if not isinstance(tool_result, dict):
            text = str(tool_result or "").strip()
            payload = {"text": text} if text else {}
            return text, ui, payload

        raw_payload = tool_result.get("payload")
        if isinstance(raw_payload, dict):
            payload = dict(raw_payload)

        ui_candidate = tool_result.get("ui")
        if not isinstance(ui_candidate, dict) and isinstance(payload.get("ui"), dict):
            ui_candidate = payload.get("ui")
        if isinstance(ui_candidate, dict):
            ui = ui_candidate

        text_candidates = [
            payload.get("text"),
            tool_result.get("text"),
            tool_result.get("result"),
            tool_result.get("message"),
            tool_result.get("summary"),
        ]
        for value in text_candidates:
            rendered = str(value or "").strip()
            if rendered:
                text = rendered
                break

        if text and "text" not in payload:
            payload["text"] = text
        if ui and "ui" not in payload:
            payload["ui"] = ui
        return text, ui, payload

    @staticmethod
    def _build_tool_signature(function_calls) -> str:
        signatures: list[str] = []
        for fc in function_calls:
            try:
                args_str = json.dumps(fc.args or {}, ensure_ascii=False, sort_keys=True)
            except Exception:
                args_str = str(fc.args)
            signatures.append(f"{fc.name}:{args_str}")
        return "|".join(signatures)
