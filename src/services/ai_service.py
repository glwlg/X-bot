import logging
import inspect
import os
import json
import asyncio
from typing import Any, Awaitable, Callable, cast

from core.config import GEMINI_MODEL, openai_async_client
from services.openai_adapter import build_messages

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
    Service for interacting with OpenAI chat models, acting as a generic Agent Engine.
    Handles:
    - Text generation
    - Tool use (Function Calling) loop
    - Streaming responses
    """

    async def generate_response_stream(
        self,
        message_history: list,
        tools: list | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
        system_instruction: str | None = None,
        event_callback: Callable[[str, dict[str, Any]], Any] | None = None,
    ):
        """
        Generator for streaming responses with support for Function Calling (Agent Loop).

        Args:
            message_history: List of history content objects/dicts.
            tools: List of function declaration objects.
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
        try:
            MAX_TOOL_CALLS_PER_TOOL = max(
                1,
                int(os.getenv("AI_TOOL_MAX_CALLS_PER_TOOL", "10")),
            )
        except ValueError:
            MAX_TOOL_CALLS_PER_TOOL = 10
        try:
            MAX_SEMANTIC_REPEAT_TOOL_CALLS = max(
                2,
                int(os.getenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "3")),
            )
        except ValueError:
            MAX_SEMANTIC_REPEAT_TOOL_CALLS = 3
        turn_count = 0
        completed = False
        has_tool_call = False
        pending_tool_failures: list[str] = []
        last_tool_signature = ""
        last_semantic_tool_signature = ""
        repeat_tool_call_count = 0
        repeat_semantic_tool_call_count = 0
        per_tool_call_count: dict[str, int] = {}
        last_terminal_success_text = ""
        last_terminal_success_summary = ""
        last_terminal_tool_name = ""

        current_history = build_messages(
            contents=message_history,
            system_instruction=system_instruction,
        )
        openai_tools = self._build_openai_tools(tools)
        client = openai_async_client

        try:

            async def _emit(event: str, payload: dict[str, Any]):
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

            async def _synthesize_async_dispatch_notice(
                dispatch_rows: list[dict[str, str]],
            ) -> str:
                if client is None:
                    return ""
                compact: list[str] = []
                for row in dispatch_rows[:3]:
                    worker_name = str(row.get("worker_name") or "").strip()
                    task_id = str(row.get("task_id") or "").strip()
                    if worker_name and task_id:
                        compact.append(f"{worker_name}（任务 {task_id}）")
                    elif task_id:
                        compact.append(f"任务 {task_id}")
                    elif worker_name:
                        compact.append(worker_name)

                guidance = (
                    "系统提示：你刚刚通过工具成功派发了异步执行任务。"
                    "现在请只向用户回复任务已开始处理的进度说明（1-2句中文）。"
                    "必须提到任务编号；若有执行助手名称也请提到。"
                    "不要输出任务最终结论，不要编造天气/数据结果，不要假装任务已完成。"
                    "派发信息：" + ("；".join(compact) if compact else "已派发")
                )
                synth_history = list(current_history)
                synth_history.append({"role": "user", "content": guidance})
                try:
                    synth_response = await cast(Any, client).chat.completions.create(
                        model=GEMINI_MODEL,
                        messages=synth_history,
                    )
                except Exception as exc:
                    logger.warning(
                        "[AiService] Async-dispatch synthesis failed: %s", exc
                    )
                    return ""
                return self._extract_response_text(synth_response).strip()

            def _build_async_dispatch_fallback(
                dispatch_rows: list[dict[str, str]],
            ) -> str:
                first = dispatch_rows[0] if dispatch_rows else {}
                worker_name = str(first.get("worker_name") or "执行助手").strip()
                task_id = str(first.get("task_id") or "").strip()
                if task_id:
                    return (
                        f"已派发给 {worker_name} 处理（任务 {task_id}），"
                        "正在处理中，完成后会自动把结果发给你。"
                    )
                return (
                    f"已派发给 {worker_name} 处理，"
                    "正在处理中，完成后会自动把结果发给你。"
                )

            async def _synthesize_final_after_guard(*, guard_reason: str) -> str:
                if client is None:
                    return ""
                guidance = (
                    "系统提示：工具调用已触发保护阈值（"
                    f"{guard_reason}"
                    "），请不要再调用任何工具。"
                    "请基于当前已获得的工具结果直接给出最终答复；"
                    "若信息不足，请明确缺失项并给出下一步建议。"
                )
                synth_history = list(current_history)
                synth_history.append({"role": "user", "content": guidance})
                try:
                    synth_response = await cast(Any, client).chat.completions.create(
                        model=GEMINI_MODEL,
                        messages=synth_history,
                    )
                except Exception as exc:
                    logger.warning(
                        "[AiService] Guard synthesis failed (%s): %s",
                        guard_reason,
                        exc,
                    )
                    return ""
                return self._extract_response_text(synth_response).strip()

            while turn_count < MAX_TURNS:
                turn_count += 1
                await _emit("turn_start", {"turn": turn_count})

                if client is None:
                    raise RuntimeError("OpenAI async client is not initialized")

                if tools:
                    logger.debug(
                        f"🤖 [AiService] Sending prompt to AI (Tools Mode):\n{current_history}"
                    )
                    request_kwargs: dict[str, Any] = {
                        "model": GEMINI_MODEL,
                        "messages": current_history,
                    }
                    if openai_tools:
                        request_kwargs["tools"] = openai_tools
                    response = await cast(Any, client).chat.completions.create(
                        **request_kwargs
                    )
                    function_calls = self._extract_tool_calls(response)

                    if function_calls:
                        # Agent decided to act
                        has_tool_call = True
                        guarded_calls = [
                            item
                            for item in function_calls
                            if self._should_apply_cost_guards(
                                str(item.get("name") or "")
                            )
                        ]

                        if guarded_calls:
                            semantic_signature = self._build_tool_signature(
                                guarded_calls,
                                semantic=True,
                            )
                            if semantic_signature == last_semantic_tool_signature:
                                repeat_semantic_tool_call_count += 1
                            else:
                                last_semantic_tool_signature = semantic_signature
                                repeat_semantic_tool_call_count = 1

                            if (
                                repeat_semantic_tool_call_count
                                >= MAX_SEMANTIC_REPEAT_TOOL_CALLS
                            ):
                                await _emit(
                                    "semantic_loop_guard",
                                    {
                                        "turn": turn_count,
                                        "repeat_count": repeat_semantic_tool_call_count,
                                        "tool_names": [
                                            str(item.get("name") or "")
                                            for item in guarded_calls
                                        ],
                                        "signature": semantic_signature,
                                    },
                                )
                                fallback_text = last_terminal_success_text or (
                                    "⚠️ 检测到语义上重复的工具调用，已停止继续搜索。"
                                    "自动整理最终结论失败，请重试或缩小查询范围。"
                                )
                                final_text = (
                                    await _synthesize_final_after_guard(
                                        guard_reason="semantic_loop_guard"
                                    )
                                ) or fallback_text
                                await _emit(
                                    "final_response",
                                    {
                                        "turn": turn_count,
                                        "text_preview": final_text.replace("\n", " ")[
                                            :200
                                        ],
                                        "source": "semantic_loop_guard",
                                    },
                                )
                                if tools and tool_final_stream_enabled:
                                    for segment in _split_text_for_streaming(
                                        final_text,
                                        tool_final_stream_chunk_chars,
                                    ):
                                        yield segment
                                else:
                                    yield final_text
                                completed = True
                                break

                            projected_tool_count = dict(per_tool_call_count)
                            for item in guarded_calls:
                                tool_name = str(item.get("name") or "").strip()
                                if not tool_name:
                                    continue
                                projected_tool_count[tool_name] = (
                                    int(projected_tool_count.get(tool_name) or 0) + 1
                                )

                            exceeded_tools: list[str] = []
                            guarded_names = {
                                str(item.get("name") or "").strip()
                                for item in guarded_calls
                            }
                            for name in guarded_names:
                                if not name:
                                    continue
                                current_count = int(per_tool_call_count.get(name) or 0)
                                next_count = int(projected_tool_count.get(name) or 0)
                                if (
                                    current_count >= MAX_TOOL_CALLS_PER_TOOL
                                    and next_count > current_count
                                ):
                                    exceeded_tools.append(name)

                            if exceeded_tools:
                                await _emit(
                                    "tool_budget_guard",
                                    {
                                        "turn": turn_count,
                                        "limit": MAX_TOOL_CALLS_PER_TOOL,
                                        "tools": exceeded_tools,
                                        "counts": {
                                            name: int(
                                                projected_tool_count.get(name) or 0
                                            )
                                            for name in exceeded_tools
                                        },
                                    },
                                )
                                fallback_text = last_terminal_success_text or (
                                    "⚠️ 已达到单工具调用上限，停止继续重复调用。"
                                    "自动整理最终结论失败，请重试或缩小查询范围。"
                                )
                                final_text = (
                                    await _synthesize_final_after_guard(
                                        guard_reason="tool_budget_guard"
                                    )
                                ) or fallback_text
                                await _emit(
                                    "final_response",
                                    {
                                        "turn": turn_count,
                                        "text_preview": final_text.replace("\n", " ")[
                                            :200
                                        ],
                                        "source": "tool_budget_guard",
                                    },
                                )
                                if tools and tool_final_stream_enabled:
                                    for segment in _split_text_for_streaming(
                                        final_text,
                                        tool_final_stream_chunk_chars,
                                    ):
                                        yield segment
                                else:
                                    yield final_text
                                completed = True
                                break
                        else:
                            last_semantic_tool_signature = ""
                            repeat_semantic_tool_call_count = 0

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
                                "tool_names": [
                                    str(item.get("name") or "")
                                    for item in function_calls
                                ],
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
                            "[AiService] Agent decided to call: %s",
                            [str(item.get("name") or "") for item in function_calls],
                        )

                        assistant_tool_message = self._build_assistant_tool_message(
                            response
                        )
                        if assistant_tool_message:
                            current_history.append(assistant_tool_message)

                        # Execute tools
                        turn_failures: list[str] = []
                        async_dispatch_rows: list[dict[str, str]] = []
                        terminal_short_circuit_text = ""
                        should_terminal_stop = False
                        for index, fc in enumerate(function_calls):
                            tool_name = str(fc.get("name") or "").strip()
                            tool_args = fc.get("args")
                            if not isinstance(tool_args, dict):
                                tool_args = {}
                            tool_call_id = str(fc.get("id") or "").strip() or (
                                f"call_{turn_count}_{index + 1}"
                            )
                            if tool_executor:
                                await _emit(
                                    "tool_call_started",
                                    {
                                        "turn": turn_count,
                                        "name": tool_name,
                                        "args": tool_args,
                                    },
                                )

                                try:
                                    logger.info(
                                        f"Executing tool: {tool_name} args={tool_args}"
                                    )
                                    tool_result = await asyncio.wait_for(
                                        tool_executor(tool_name, tool_args),
                                        timeout=float(TOOL_EXEC_TIMEOUT_SEC),
                                    )
                                except asyncio.TimeoutError:
                                    logger.error(
                                        f"Tool execution timed out: {tool_name}"
                                    )
                                    tool_result = (
                                        f"Error: Tool '{tool_name}' timed out after "
                                        f"{TOOL_EXEC_TIMEOUT_SEC} seconds."
                                    )
                                except Exception as e:
                                    logger.error(f"Tool execution error: {e}")
                                    tool_result = (
                                        f"Error executing tool {tool_name}: {str(e)}"
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
                                    last_terminal_tool_name = tool_name

                                if not tool_ok:
                                    turn_failures.append(
                                        f"{tool_name}: {self._summarize_tool_result(tool_result)}"
                                    )
                                directive = await _emit(
                                    "tool_call_finished",
                                    {
                                        "turn": turn_count,
                                        "name": tool_name,
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
                                current_history.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tool_call_id,
                                        "content": json.dumps(
                                            {
                                                "result": self._sanitize_tool_result_for_history(
                                                    tool_result
                                                )
                                            },
                                            ensure_ascii=False,
                                            default=str,
                                        ),
                                    }
                                )
                                if self._should_apply_cost_guards(tool_name):
                                    per_tool_call_count[tool_name] = (
                                        int(per_tool_call_count.get(tool_name) or 0) + 1
                                    )

                                if (
                                    tool_ok
                                    and isinstance(tool_result, dict)
                                    and bool(tool_result.get("async_dispatch"))
                                ):
                                    async_dispatch_rows.append(
                                        {
                                            "tool_name": tool_name,
                                            "worker_name": str(
                                                tool_result.get("worker_name") or ""
                                            ).strip(),
                                            "task_id": str(
                                                tool_result.get("task_id") or ""
                                            ).strip(),
                                        }
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

                        if async_dispatch_rows:
                            notice_text = (
                                await _synthesize_async_dispatch_notice(
                                    async_dispatch_rows
                                )
                            ) or _build_async_dispatch_fallback(async_dispatch_rows)
                            await _emit(
                                "final_response",
                                {
                                    "turn": turn_count,
                                    "text_preview": notice_text.replace("\n", " ")[
                                        :200
                                    ],
                                    "source": "async_dispatch",
                                },
                            )
                            if tools and tool_final_stream_enabled:
                                for segment in _split_text_for_streaming(
                                    notice_text,
                                    tool_final_stream_chunk_chars,
                                ):
                                    yield segment
                            else:
                                yield notice_text
                            completed = True
                            break

                        # Continue to next turn (ReAct loop)
                        continue

                    else:
                        model_text = self._extract_response_text(response)

                        if (
                            has_tool_call
                            and pending_tool_failures
                            and turn_count < MAX_TURNS
                        ):
                            logger.info(
                                "[AiService] Tool failure detected; forcing another attempt. failures=%s",
                                pending_tool_failures,
                            )
                            assistant_text_message = self._build_assistant_text_message(
                                response
                            )
                            if assistant_text_message:
                                current_history.append(assistant_text_message)
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
                                {"role": "user", "content": recovery_instruction}
                            )
                            continue

                        # Agent decided to reply with text (Final Answer)
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
                            logger.warning("[AiService] Empty text response.")
                            await _emit(
                                "final_response",
                                {
                                    "turn": turn_count,
                                    "text_preview": "",
                                },
                            )
                            yield "⚠️ 抱歉，模型返回了空响应，可能是触发了安全过滤或内部错误。"
                        completed = True
                        break

                else:
                    logger.debug(
                        f"🤖 [AiService] Sending prompt to AI (Stream Mode):\n{current_history}"
                    )
                    stream = await cast(Any, client).chat.completions.create(
                        model=GEMINI_MODEL,
                        messages=current_history,
                        stream=True,
                    )
                    async for chunk in stream:
                        chunk_text = self._extract_stream_text(chunk)
                        if chunk_text:
                            yield chunk_text
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
    def _build_openai_tools(tools: list | None) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            parameters = tool.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}}
            output.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(tool.get("description") or ""),
                        "parameters": parameters,
                    },
                }
            )
        return output

    @staticmethod
    def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
        choices = list(getattr(response, "choices", []) or [])
        if not choices:
            return []
        message = getattr(choices[0], "message", None)
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        output: list[dict[str, Any]] = []
        for item in tool_calls:
            function = getattr(item, "function", None)
            name = str(getattr(function, "name", "") or "").strip()
            if not name:
                continue
            raw_args = getattr(function, "arguments", "") or ""
            parsed_args: dict[str, Any] = {}
            if isinstance(raw_args, str) and raw_args.strip():
                try:
                    loaded = json.loads(raw_args)
                    if isinstance(loaded, dict):
                        parsed_args = loaded
                except Exception:
                    parsed_args = {}
            output.append(
                {
                    "id": str(getattr(item, "id", "") or "").strip(),
                    "name": name,
                    "args": parsed_args,
                }
            )
        return output

    @staticmethod
    def _build_assistant_tool_message(response: Any) -> dict[str, Any] | None:
        choices = list(getattr(response, "choices", []) or [])
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if not tool_calls:
            return None
        payload_calls: list[dict[str, Any]] = []
        for call in tool_calls:
            function = getattr(call, "function", None)
            name = str(getattr(function, "name", "") or "").strip()
            if not name:
                continue
            payload_calls.append(
                {
                    "id": str(getattr(call, "id", "") or "").strip(),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": str(getattr(function, "arguments", "") or ""),
                    },
                }
            )
        if not payload_calls:
            return None
        message_content = getattr(message, "content", "")
        return {
            "role": "assistant",
            "content": str(message_content or ""),
            "tool_calls": payload_calls,
        }

    @staticmethod
    def _build_assistant_text_message(response: Any) -> dict[str, Any] | None:
        text = AiService._extract_response_text(response)
        if not text:
            return None
        return {"role": "assistant", "content": text}

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        choices = list(getattr(response, "choices", []) or [])
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    chunks.append(str(item.get("text") or ""))
            return "".join(chunks).strip()
        return ""

    @staticmethod
    def _extract_stream_text(chunk: Any) -> str:
        choices = list(getattr(chunk, "choices", []) or [])
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    chunks.append(str(item.get("text") or ""))
                    continue
                text = getattr(item, "text", None)
                if text:
                    chunks.append(str(text))
            return "".join(chunks)
        return ""

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
    def _sanitize_tool_result_for_history(tool_result: Any) -> Any:
        def _sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                sanitized: dict[str, Any] = {}
                for key, item in value.items():
                    key_text = str(key)
                    if key_text == "files" and isinstance(item, dict):
                        names = [str(name) for name in list(item.keys())[:8]]
                        sanitized[key_text] = {
                            "count": len(item),
                            "names": names,
                        }
                        continue
                    sanitized[key_text] = _sanitize(item)
                return sanitized
            if isinstance(value, list):
                return [_sanitize(item) for item in value[:50]]
            if isinstance(value, tuple):
                return [_sanitize(item) for item in list(value)[:50]]
            if isinstance(value, (bytes, bytearray)):
                return f"<binary:{len(value)} bytes>"
            return value

        return _sanitize(tool_result)

    @staticmethod
    def _build_tool_signature(function_calls, *, semantic: bool = False) -> str:
        def _normalize_value(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    str(k): _normalize_value(v)
                    for k, v in sorted(value.items(), key=lambda item: str(item[0]))
                }
            if isinstance(value, list):
                return [_normalize_value(item) for item in value]
            if isinstance(value, str):
                text = value.strip().lower()
                if semantic:
                    text = " ".join(text.split())
                    text = text.replace("https://", "").replace("http://", "")
                return text
            return value

        signatures: list[str] = []
        for fc in function_calls:
            name = ""
            args_obj: Any = {}
            if isinstance(fc, dict):
                name = str(fc.get("name") or "").strip()
                args_obj = fc.get("args")
            else:
                name = str(getattr(fc, "name", "") or "").strip()
                args_obj = getattr(fc, "args", {})
            try:
                args_str = json.dumps(
                    _normalize_value(args_obj or {}), ensure_ascii=False, sort_keys=True
                )
            except Exception:
                args_str = str(args_obj)
            signatures.append(f"{name}:{args_str}")
        return "|".join(signatures)

    @staticmethod
    def _should_apply_cost_guards(tool_name: str) -> bool:
        name = str(tool_name or "").strip().lower()
        return bool(name) and name.startswith("ext_")
