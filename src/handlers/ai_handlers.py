import time
import asyncio
import logging
import base64
import json
import re
import shlex
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from core.platform.models import UnifiedContext, MessageType
import random

from core.config import (
    gemini_client,
    GEMINI_MODEL,
    ROUTING_MODEL,
    CORE_CHAT_EXECUTION_MODE,
    CORE_CHAT_WORKER_BACKEND,
)
from core.platform.exceptions import MediaProcessingError, MessageSendError

from user_context import get_user_context, add_message
from repositories import get_user_settings
from stats import increment_stat
from core.prompt_composer import prompt_composer
from services.intent_router import intent_router
from .media_utils import extract_media_input
from .message_utils import process_and_send_code_files

logger = logging.getLogger(__name__)

# 思考提示消息
THINKING_MESSAGE = "🤔 让我想想..."
LONG_RESPONSE_FILE_THRESHOLD = 9000
WORKER_PROGRESS_INTERVAL_SEC = 10
SHELL_COMMAND_HINTS = {
    "echo",
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "git",
    "docker",
    "uv",
    "python",
    "python3",
    "pip",
    "npm",
    "pnpm",
    "yarn",
    "bash",
    "sh",
    "zsh",
    "curl",
    "wget",
    "make",
    "pytest",
}


@dataclass
class _WorkerDelegateSession:
    user_id: str
    worker_id: str
    backend: str
    instruction_preview: str
    started_at: float
    task: asyncio.Task
    done_event: asyncio.Event = field(default_factory=asyncio.Event)


_WORKER_DELEGATE_SESSIONS: dict[str, _WorkerDelegateSession] = {}
_WORKER_DELEGATE_LOCK = asyncio.Lock()
_WORKER_PROGRESS_PHRASES = [
    "我在持续跟进这个任务",
    "Worker 正在处理中",
    "还在执行关键步骤",
    "我在等待 Worker 返回最终结果",
    "任务还在跑，我会第一时间同步",
]
CAPABILITY_GAP_HINTS = (
    "无法直接获取",
    "无法实时",
    "没有实时",
    "当前无法联网",
    "不具备联网",
    "不能直接抓取",
    "can't access",
    "cannot access",
    "real-time news",
    "no realtime",
)


def _instruction_preview(text: str, limit: int = 72) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "..."


def _is_worker_status_query(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    tokens = (
        "进度",
        "状态",
        "完成了吗",
        "好了没",
        "好了吗",
        "还在吗",
        "多久",
        "还没好",
        "还没完成",
        "status",
        "progress",
    )
    return any(token in lowered for token in tokens)


async def _get_running_worker_session(user_id: str) -> _WorkerDelegateSession | None:
    async with _WORKER_DELEGATE_LOCK:
        session = _WORKER_DELEGATE_SESSIONS.get(str(user_id))
        if not session:
            return None
        if session.task.done() or session.done_event.is_set():
            _WORKER_DELEGATE_SESSIONS.pop(str(user_id), None)
            return None
        return session


async def _set_worker_session(user_id: str, session: _WorkerDelegateSession) -> None:
    async with _WORKER_DELEGATE_LOCK:
        _WORKER_DELEGATE_SESSIONS[str(user_id)] = session


async def _clear_worker_session(user_id: str) -> None:
    async with _WORKER_DELEGATE_LOCK:
        _WORKER_DELEGATE_SESSIONS.pop(str(user_id), None)


async def _worker_progress_pulse(ctx: UnifiedContext, session: _WorkerDelegateSession) -> None:
    i = 0
    while not session.done_event.is_set():
        await asyncio.sleep(WORKER_PROGRESS_INTERVAL_SEC)
        if session.done_event.is_set():
            break
        elapsed = max(1, int(time.time() - session.started_at))
        phrase = _WORKER_PROGRESS_PHRASES[i % len(_WORKER_PROGRESS_PHRASES)]
        i += 1
        text = (
            f"⏳ {phrase}（已用时 {elapsed}s）\n"
            f"- worker: `{session.worker_id}`\n"
            f"- 正在处理：{session.instruction_preview}"
        )
        try:
            await ctx.reply(text)
        except Exception:
            logger.debug("Worker progress pulse send failed.", exc_info=True)


def _build_manager_note(instruction: str, result_text: str) -> str:
    intent = str(instruction or "").lower()
    text = str(result_text or "").strip()
    if not text:
        return ""
    if any(token in intent for token in ("新闻", "news", "最新", "今天")):
        return "如需我按地区或主题再筛一轮，我可以继续整理成更短的清单。"
    if any(token in intent for token in ("部署", "发布", "deploy")):
        return "如果你愿意，我可以继续做一次部署后巡检（端口、日志、健康检查）。"
    return ""


def _format_worker_final_reply(*, elapsed: int, output: str, manager_note: str) -> str:
    base = f"✅ Worker 执行完成（耗时 {elapsed}s）\n\n{output}"
    note = str(manager_note or "").strip()
    if not note:
        return base
    return f"{base}\n\n【Manager 补充】{note}"


def _join_notes(*notes: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for note in notes:
        text = str(note or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(text)
    return "\n".join(lines).strip()


def _normalize_groups(items: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw in (items or []):
        token = str(raw or "").strip().lower()
        if token and token not in normalized:
            normalized.append(token)
    return normalized


async def _maybe_recover_worker_failure_with_coding(
    *,
    worker_id: str,
    backend: str,
    instruction: str,
    metadata: dict[str, Any],
    initial_result: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """
    当 Worker 因策略缺失无法调用编码后端时，自动为该 Worker 开启编码组并重试一次。
    """
    error_code = str(initial_result.get("error") or "").strip().lower()
    selected_backend = str(initial_result.get("backend") or backend or "").strip().lower()
    if error_code != "policy_blocked":
        return None, ""
    if selected_backend not in {"codex", "gemini-cli"}:
        return None, ""

    from core.tool_access_store import tool_access_store
    from core.worker_runtime import worker_runtime

    policy = tool_access_store.get_worker_policy(worker_id)
    tools_cfg = dict((policy or {}).get("tools") or {})
    allow = _normalize_groups(tools_cfg.get("allow"))
    deny = _normalize_groups(tools_cfg.get("deny"))

    changed = False
    if "group:all" not in allow and "group:coding" not in allow:
        allow.append("group:coding")
        changed = True
    if "group:coding" in deny:
        deny = [item for item in deny if item != "group:coding"]
        changed = True
    if not changed:
        return None, ""

    ok, reason = tool_access_store.set_worker_policy(
        worker_id,
        allow=allow,
        deny=deny,
        actor="core-manager",
    )
    if not ok:
        return None, f"尝试为 Worker 增加编码能力失败：{reason}"

    retry_meta = dict(metadata or {})
    retry_meta["manager_recovery"] = {
        "type": "coding_capability_uplift",
        "reason": "worker_policy_blocked",
    }
    retry = await worker_runtime.execute_task(
        worker_id=worker_id,
        source="user_chat",
        instruction=instruction,
        backend=selected_backend,
        metadata=retry_meta,
    )
    note = (
        f"检测到 Worker 缺少编码能力，已为 `{worker_id}` 开启 `group:coding` 并自动重试一次。"
    )
    return retry, note


async def _run_worker_task_background(
    *,
    ctx: UnifiedContext,
    user_id: str,
    worker_id: str,
    backend: str,
    instruction: str,
    metadata: dict,
    session: _WorkerDelegateSession,
) -> None:
    from core.worker_runtime import worker_runtime

    progress_task = asyncio.create_task(
        _worker_progress_pulse(ctx, session),
        name=f"worker-progress-{user_id}",
    )
    started = time.time()
    try:
        try:
            result = await worker_runtime.execute_task(
                worker_id=worker_id,
                source="user_chat",
                instruction=instruction,
                backend=backend,
                metadata=metadata,
            )
        except Exception as exc:
            logger.error("Worker delegation background error: %s", exc, exc_info=True)
            result = {
                "ok": False,
                "task_id": "",
                "backend": backend,
                "error": "delegate_runtime_error",
                "summary": str(exc),
            }
    finally:
        session.done_event.set()
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task

    recovery_note = ""
    if not result.get("ok"):
        try:
            recovered, note = await _maybe_recover_worker_failure_with_coding(
                worker_id=worker_id,
                backend=backend,
                instruction=instruction,
                metadata=metadata,
                initial_result=result,
            )
            if recovered is not None:
                result = recovered
            recovery_note = note
        except Exception:
            logger.error("Worker recovery attempt failed.", exc_info=True)

    elapsed = max(1, int(time.time() - started))
    if result.get("ok"):
        output = str(result.get("result") or result.get("summary") or "").strip()
        if not output:
            output = "✅ Worker 已完成执行。"
        manager_note = _join_notes(
            recovery_note,
            _build_manager_note(instruction=instruction, result_text=output),
        )
        if len(output) > LONG_RESPONSE_FILE_THRESHOLD:
            await ctx.reply(
                "✅ Worker 执行完成（耗时 "
                f"{elapsed}s），结果较长，正在发送文件。"
            )
            await _send_response_as_markdown_file(ctx, output, prefix="worker_response")
            if manager_note:
                await ctx.reply(f"【Manager 补充】{manager_note}")
        else:
            await ctx.reply(
                _format_worker_final_reply(
                    elapsed=elapsed,
                    output=output,
                    manager_note=manager_note,
                )
            )
        try:
            stored_text = (
                output if not manager_note else f"{output}\n\n【Manager 补充】{manager_note}"
            )
            await add_message(
                ctx,
                user_id,
                "model",
                stored_text,
            )
        except Exception:
            logger.debug("Failed to persist worker final output.", exc_info=True)
    else:
        fail_detail = str(result.get("summary", "") or "").strip()
        note = _join_notes(recovery_note)
        if note:
            fail_detail = _join_notes(note, fail_detail)
        await ctx.reply(
            "❌ Worker 执行失败\n"
            f"- task_id: `{result.get('task_id', '')}`\n"
            f"- backend: `{result.get('backend', backend)}`\n"
            f"- error: `{result.get('error', 'unknown')}`\n"
            f"- elapsed: `{elapsed}s`\n\n"
            f"{fail_detail}"
        )
    await _clear_worker_session(user_id)


def _looks_like_shell_command(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or "\n" in raw:
        return False
    try:
        parts = shlex.split(raw)
    except Exception:
        return False
    if not parts:
        return False
    first = parts[0]
    if first in SHELL_COMMAND_HINTS:
        return True
    return first.startswith("./") or first.startswith("../") or first.startswith("/")


def _is_lightweight_chat_utterance(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or "\n" in raw:
        return False
    if _looks_like_shell_command(raw):
        return False
    if any(token in raw for token in ("http://", "https://", "```", "{", "}", "$(", "&&", "||")):
        return False
    return len(raw) <= 4


def _response_indicates_capability_gap(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in CAPABILITY_GAP_HINTS)


def _extract_history_text(item: Any) -> tuple[str, str]:
    role = ""
    parts = []
    if isinstance(item, dict):
        role = str(item.get("role") or "").strip().lower()
        parts = item.get("parts") or []
    else:
        role = str(getattr(item, "role", "") or "").strip().lower()
        parts = getattr(item, "parts", []) or []
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = str(part.get("text") or "").strip()
            if text:
                texts.append(text)
        else:
            text = str(getattr(part, "text", "") or "").strip()
            if text:
                texts.append(text)
    return role, "\n".join(texts).strip()


def _compact_text(text: str, limit: int = 220) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "..."


async def _should_include_memory_summary_for_task(user_message: str, dialog_context: str) -> bool:
    request = str(user_message or "").strip()
    if not request:
        return False
    history = str(dialog_context or "").strip()
    if len(request) <= 6:
        return True

    prompt = (
        "You are a routing helper for a manager-worker assistant.\n"
        "Decide whether the manager should attach a user memory summary for a worker task.\n"
        "Attach memory summary when task quality depends on user-specific profile/history "
        "(identity, location, preferences, routines, personal context, timezone).\n"
        "Do NOT attach memory summary when task is fully self-contained.\n"
        "Return JSON only with keys: need_memory_summary (boolean), confidence (0..1), reason.\n"
        f"request: {request}\n"
        f"recent_dialog_context: {history[:1200]}"
    )
    try:
        response = await gemini_client.aio.models.generate_content(
            model=ROUTING_MODEL,
            contents=prompt,
            config={"temperature": 0},
        )
        raw = str(response.text or "").strip()
        parsed: dict[str, Any] = {}
        try:
            parsed = json.loads(raw)
        except Exception:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                with contextlib.suppress(Exception):
                    parsed = json.loads(match.group(0))
        need = bool(parsed.get("need_memory_summary"))
        confidence = float(parsed.get("confidence", 0.0) or 0.0)
        if need:
            return True
        if confidence < 0.35:
            return True
        return False
    except Exception:
        return len(request) <= 12


async def _collect_recent_dialog_context(
    ctx: UnifiedContext,
    *,
    user_id: str,
    current_user_message: str,
    max_messages: int = 6,
    max_chars: int = 1200,
) -> str:
    try:
        history = await get_user_context(ctx, user_id)
    except Exception:
        return ""
    if not history:
        return ""

    current_norm = " ".join(str(current_user_message or "").split())
    skipped_current = False
    lines: list[str] = []
    for item in reversed(history):
        role, text = _extract_history_text(item)
        if not text:
            continue
        text_norm = " ".join(text.split())
        if not skipped_current and role == "user" and text_norm == current_norm:
            skipped_current = True
            continue
        role_label = "用户" if role == "user" else "助手"
        lines.append(f"- {role_label}: {_compact_text(text)}")
        if len(lines) >= max_messages:
            break

    if not lines:
        return ""
    lines.reverse()
    joined = "\n".join(lines)
    if len(joined) > max_chars:
        joined = joined[-max_chars:]
    return joined.strip()


async def _build_worker_instruction_with_context(
    ctx: UnifiedContext,
    *,
    user_id: str,
    user_message: str,
    worker_has_memory: bool,
) -> tuple[str, dict[str, Any]]:
    dialog_context = await _collect_recent_dialog_context(
        ctx,
        user_id=user_id,
        current_user_message=user_message,
    )
    wants_memory_summary = await _should_include_memory_summary_for_task(
        user_message,
        dialog_context,
    )
    memory_snapshot = ""
    if wants_memory_summary and not worker_has_memory:
        memory_snapshot = await _fetch_user_memory_snapshot(user_id)

    sections: list[str] = [
        "你是由 Core Manager 派发的 Worker 执行器，请完成任务后返回给 Manager。",
        "【Worker 能力边界】\n"
        "- 你不能直接与最终用户沟通。\n"
        "- 你当前没有 memory 工具，不能自行检索用户长期记忆；仅可使用 Manager 提供的记忆摘要。\n"
        "- 若信息不足，请在结果末尾用“缺失信息：...”列出最小必要项。",
        f"【当前用户请求】\n{str(user_message or '').strip()}",
    ]
    if dialog_context:
        sections.append(f"【近期对话上下文】\n{dialog_context}")
    if memory_snapshot:
        sections.append(f"【用户记忆摘要（由 Manager 提供）】\n{memory_snapshot}")
    sections.append(
        "【交付要求】\n"
        "- 直接给出可执行结果或结论。\n"
        "- 不要重复系统边界说明。\n"
        "- 输出应可被 Manager 直接转述给用户。"
    )
    instruction = "\n\n".join([item for item in sections if str(item).strip()]).strip()
    if len(instruction) > 6000:
        instruction = instruction[:6000]
    return instruction, {
        "worker_has_memory": worker_has_memory,
        "dialog_context_included": bool(dialog_context),
        "memory_summary_included": bool(memory_snapshot),
        "memory_summary_requested": bool(wants_memory_summary),
    }


def _resolve_worker_delegate_mode(config_mode: str, is_task: bool) -> str:
    if not is_task:
        return ""
    mode = str(config_mode or "").strip().lower() or "worker_only"
    if mode in {"worker_only", "worker_preferred"}:
        return mode
    if mode == "orchestrator" and is_task:
        return "worker_only"
    return ""


def _is_message_too_long_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "too_long" in text or "too long" in text or "message is too long" in text


async def _send_response_as_markdown_file(
    ctx: UnifiedContext, content: str, prefix: str = "agent_response"
):
    if not content:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{stamp}.md"
    return await ctx.reply_document(
        document=content.encode("utf-8"),
        filename=filename,
        caption="📝 内容较长，已转为 Markdown 文件发送。",
    )


def _extract_text_from_mcp_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        texts: list[str] = []
        for item in payload:
            if isinstance(item, dict):
                text_val = item.get("text")
                if text_val:
                    texts.append(str(text_val))
            elif hasattr(item, "text") and getattr(item, "text"):
                texts.append(str(getattr(item, "text")))
        if texts:
            return "\n".join(texts)
    if isinstance(payload, dict):
        if payload.get("text"):
            return str(payload.get("text"))
    return str(payload)


def _parse_memory_graph_payload(payload: Any) -> dict[str, Any]:
    text = _extract_text_from_mcp_payload(payload).strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except Exception:
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _render_user_memory_snapshot(graph: dict[str, Any]) -> str:
    if not isinstance(graph, dict):
        return ""
    entities = graph.get("entities")
    relations = graph.get("relations")
    if not isinstance(entities, list):
        entities = []
    if not isinstance(relations, list):
        relations = []

    entity_by_name: dict[str, dict[str, Any]] = {}
    for item in entities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            entity_by_name[name] = item

    lines: list[str] = []
    user_entity = entity_by_name.get("User")
    user_obs = []
    if isinstance(user_entity, dict):
        raw_obs = user_entity.get("observations")
        if isinstance(raw_obs, list):
            user_obs = [str(item).strip() for item in raw_obs if str(item or "").strip()]
    if user_obs:
        lines.extend([f"- {item}" for item in user_obs])

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        frm = str(rel.get("from") or "").strip()
        if frm != "User":
            continue
        to_name = str(rel.get("to") or "").strip()
        rel_type = str(rel.get("relationType") or "").strip()
        if not to_name:
            continue
        if rel_type.lower() == "lives in":
            lines.append(f"- 居住地：{to_name}")
            continue
        lines.append(f"- 关系：{rel_type or 'related to'} {to_name}")

        target = entity_by_name.get(to_name)
        if not isinstance(target, dict):
            continue
        target_obs = target.get("observations")
        if not isinstance(target_obs, list):
            continue
        for obs in target_obs[:2]:
            text = str(obs or "").strip()
            if text:
                lines.append(f"- {to_name}：{text}")

    deduped: list[str] = []
    for line in lines:
        if line and line not in deduped:
            deduped.append(line)
    return "\n".join(deduped[:40]).strip()


def _graph_has_entity(graph: dict[str, Any], entity_name: str) -> bool:
    entities = graph.get("entities") if isinstance(graph, dict) else None
    if not isinstance(entities, list):
        return False
    target = str(entity_name or "").strip()
    if not target:
        return False
    return any(
        isinstance(item, dict) and str(item.get("name") or "").strip() == target
        for item in entities
    )


def _infer_user_observations_from_graph(graph: dict[str, Any]) -> list[str]:
    inferred: list[str] = []
    relations = graph.get("relations") if isinstance(graph, dict) else None
    if isinstance(relations, list):
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            if str(rel.get("from") or "").strip() != "User":
                continue
            rel_type = str(rel.get("relationType") or "").strip().lower()
            to_name = str(rel.get("to") or "").strip()
            if rel_type == "lives in" and to_name:
                inferred.append(f"居住地：{to_name}")
    deduped: list[str] = []
    for item in inferred:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


async def _get_memory_server_for_user(user_id: str):
    from mcp_client import mcp_manager
    from mcp_client.memory import register_memory_server

    register_memory_server()
    return await mcp_manager.get_server("memory", user_id=user_id)


async def _fetch_user_memory_snapshot(user_id: str) -> str:
    try:
        server = await _get_memory_server_for_user(user_id)
        opened = await server.call_tool("open_nodes", {"names": ["User"]})
        opened_graph = _parse_memory_graph_payload(opened)
        rendered = _render_user_memory_snapshot(opened_graph)
        if rendered:
            return rendered[:2400]

        fallback = await server.call_tool("read_graph", {})
        fallback_graph = _parse_memory_graph_payload(fallback)
        rendered_fallback = _render_user_memory_snapshot(fallback_graph)
        if rendered_fallback:
            if not _graph_has_entity(fallback_graph, "User"):
                with contextlib.suppress(Exception):
                    inferred_obs = _infer_user_observations_from_graph(fallback_graph)
                    await server.call_tool(
                        "create_entities",
                        {
                            "entities": [
                                {
                                    "name": "User",
                                    "entityType": "Person",
                                    "observations": (["当前交互用户"] + inferred_obs)[:12],
                                }
                            ]
                        },
                    )
            return rendered_fallback[:2400]

        raw_fallback = _extract_text_from_mcp_payload(fallback).strip()
        if raw_fallback:
            return raw_fallback[:2400]
        raw_opened = _extract_text_from_mcp_payload(opened).strip()
        return raw_opened[:2400]
    except Exception:
        return ""


async def _try_handle_waiting_confirmation(ctx: UnifiedContext, user_message: str) -> bool:
    text = (user_message or "").strip().lower()
    if not text:
        return False

    continue_cues = {"继续", "继续执行", "继续重部署", "resume", "continue"}
    stop_cues = {"停止", "取消", "停止任务", "stop", "cancel"}
    intent_continue = text in continue_cues
    intent_stop = text in stop_cues
    if not intent_continue and not intent_stop:
        return False

    from core.heartbeat_store import heartbeat_store

    user_id = str(ctx.message.user.id)
    active_task = await heartbeat_store.get_session_active_task(user_id)
    if not active_task or active_task.get("status") != "waiting_user":
        return False

    task_id = str(active_task.get("id"))
    if intent_continue:
        await heartbeat_store.update_session_active_task(
            user_id,
            status="running",
            needs_confirmation=False,
            confirmation_deadline="",
        )
        await heartbeat_store.release_lock(user_id)
        await heartbeat_store.append_session_event(user_id, f"user_continue_by_text:{task_id}")
        await ctx.reply("✅ 已确认继续执行，正在继续处理。")
        # Let the current message continue through normal chat handling.
        return False
    else:
        await heartbeat_store.update_session_active_task(
            user_id,
            status="cancelled",
            needs_confirmation=False,
            confirmation_deadline="",
            clear_active=True,
            result_summary="Cancelled by user confirmation text.",
        )
        await heartbeat_store.release_lock(user_id)
        await heartbeat_store.append_session_event(user_id, f"user_stop_by_text:{task_id}")
        await ctx.reply("🛑 已停止该任务。")
        return True


async def _try_handle_memory_commands(ctx: UnifiedContext, user_message: str) -> bool:
    text = str(user_message or "").strip()
    if not text:
        return False
    user_id = str(ctx.message.user.id)

    explicit_patterns = (
        r"^(?:请记住|记住|记一下)\s*[:：]?\s*(.+)$",
        r"^remember\s+(.*)$",
    )

    async def _write_user_memory(content: str) -> tuple[bool, str]:
        def _split_facts(raw: str) -> list[str]:
            items = re.split(r"[，,。；;！!？?\n]+", str(raw or ""))
            return [item.strip() for item in items if str(item or "").strip()]

        def _extract_memory_observations(raw: str) -> tuple[list[str], str]:
            facts = _split_facts(raw)
            observations: list[str] = []
            location = ""
            for fact in facts:
                nickname_match = re.search(
                    r"(?:以后)?(?:请)?(?:称呼我为|叫我|喊我)([^，,。；;]+)",
                    fact,
                    flags=re.IGNORECASE,
                )
                if nickname_match:
                    nickname = str(nickname_match.group(1) or "").strip()
                    if nickname:
                        observations.append(f"偏好称呼：{nickname}")
                    continue

                location_match = re.search(
                    r"(?:我)?(?:住在|居住在|常住)([^，,。；;]+)",
                    fact,
                    flags=re.IGNORECASE,
                )
                if location_match:
                    loc = str(location_match.group(1) or "").strip()
                    if loc:
                        location = loc
                        observations.append(f"居住地：{loc}")
                    continue

                identity_match = re.search(
                    r"(?:我(?:是|是一名|是个))([^，,。；;]+)",
                    fact,
                    flags=re.IGNORECASE,
                )
                if identity_match:
                    identity = str(identity_match.group(1) or "").strip()
                    if identity:
                        observations.append(f"身份：{identity}")
                    continue

                observations.append(fact)

            if not observations:
                observations = [str(raw or "").strip()]

            deduped: list[str] = []
            for item in observations:
                if item and item not in deduped:
                    deduped.append(item)
            return deduped, location

        observations, location = _extract_memory_observations(content)
        server = await _get_memory_server_for_user(user_id)

        try:
            user_graph_raw = await server.call_tool("open_nodes", {"names": ["User"]})
            user_graph = _parse_memory_graph_payload(user_graph_raw)
            user_entities = user_graph.get("entities")
            has_user_entity = isinstance(user_entities, list) and any(
                isinstance(item, dict) and str(item.get("name") or "").strip() == "User"
                for item in user_entities
            )
            if not has_user_entity:
                await server.call_tool(
                    "create_entities",
                    {
                        "entities": [
                            {
                                "name": "User",
                                "entityType": "Person",
                                "observations": ["当前交互用户"],
                            }
                        ]
                    },
                )
            await server.call_tool(
                "add_observations",
                {
                    "observations": [
                        {
                            "entityName": "User",
                            "contents": observations,
                        }
                    ]
                },
            )
        except Exception:
            try:
                await server.call_tool(
                    "create_entities",
                    {
                        "entities": [
                            {
                                "name": "User",
                                "entityType": "Person",
                                "observations": observations,
                            }
                        ]
                    },
                )
            except Exception as exc:
                return False, str(exc)

        if location:
            with contextlib.suppress(Exception):
                location_graph_raw = await server.call_tool("open_nodes", {"names": [location]})
                location_graph = _parse_memory_graph_payload(location_graph_raw)
                location_entities = location_graph.get("entities")
                has_location = isinstance(location_entities, list) and any(
                    isinstance(item, dict) and str(item.get("name") or "").strip() == location
                    for item in location_entities
                )
                if not has_location:
                    await server.call_tool(
                        "create_entities",
                        {
                            "entities": [
                                {
                                    "name": location,
                                    "entityType": "location",
                                    "observations": [f"由用户提供的地点信息：{location}"],
                                }
                            ]
                        },
                    )
            with contextlib.suppress(Exception):
                graph_raw = await server.call_tool("read_graph", {})
                graph = _parse_memory_graph_payload(graph_raw)
                relations = graph.get("relations")
                relation_exists = isinstance(relations, list) and any(
                    isinstance(item, dict)
                    and str(item.get("from") or "").strip() == "User"
                    and str(item.get("to") or "").strip() == location
                    and str(item.get("relationType") or "").strip().lower() == "lives in"
                    for item in relations
                )
                if not relation_exists:
                    await server.call_tool(
                        "create_relations",
                        {
                            "relations": [
                                {
                                    "from": "User",
                                    "to": location,
                                    "relationType": "lives in",
                                }
                            ]
                        },
                    )

        return True, "；".join(observations[:4])

    if text.lower() in {"memory list", "memory user", "查看记忆", "我的记忆"}:
        try:
            rendered = (await _fetch_user_memory_snapshot(user_id)).strip()
            if not rendered:
                rendered = "暂未检索到用户记忆。"
            await ctx.reply(f"🧠 用户记忆\n\n{rendered}")
        except Exception as exc:
            await ctx.reply(f"⚠️ 读取记忆失败：{exc}")
        return True

    for pattern in explicit_patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        content = str(match.group(1) or "").strip()
        if not content:
            break
        ok, detail = await _write_user_memory(content)
        if ok:
            await ctx.reply(f"🧠 已通过 MCP memory 写入用户记忆。\n- 提取到：{detail}")
        else:
            await ctx.reply(f"⚠️ 写入记忆失败：{detail}")
        return True

    return False


async def _classify_dispatch_route(user_message: str) -> tuple[str, float, str]:
    if _looks_like_shell_command(user_message):
        return "worker_task", 0.95, "shell_command_fallback"

    decision = await intent_router.route(str(user_message or ""))
    route = str(getattr(decision, "route", "") or "").strip().lower()
    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    reason = str(getattr(decision, "reason", "") or "").strip()

    if route not in {"worker_task", "manager_memory", "manager_chat"}:
        route = "worker_task"
    if route == "manager_chat" and confidence < 0.85 and not _is_lightweight_chat_utterance(user_message):
        return "worker_task", max(0.6, confidence), reason or "ambiguous_chat_upgraded_to_task"
    return route, confidence, reason or "router"


async def _classify_task_intent(user_message: str) -> tuple[bool, float, str]:
    route, confidence, reason = await _classify_dispatch_route(user_message)
    return route == "worker_task", confidence, reason


async def _maybe_delegate_chat_to_worker(
    ctx: UnifiedContext,
    user_message: str,
    *,
    force: bool = False,
    force_reason: str = "",
    preclassified_route: tuple[str, float, str] | None = None,
) -> bool:
    user_id = str(ctx.message.user.id)
    running_session = await _get_running_worker_session(user_id)
    if running_session:
        if _is_worker_status_query(user_message):
            elapsed = max(1, int(time.time() - running_session.started_at))
            await ctx.reply(
                "⏳ 上一个 Worker 任务仍在执行中\n"
                f"- worker: `{running_session.worker_id}`\n"
                f"- backend: `{running_session.backend}`\n"
                f"- 已用时: `{elapsed}s`\n"
                f"- 正在处理: {running_session.instruction_preview}"
            )
            return True
        # Let manager handle non-status message while worker task continues.
        return False

    configured_mode = str(CORE_CHAT_EXECUTION_MODE or "").strip().lower() or "worker_only"
    if force:
        is_task, intent_conf, intent_reason = True, 1.0, (force_reason or "forced_delegate")
        mode = "worker_only"
    else:
        route, intent_conf, intent_reason = (
            preclassified_route
            if preclassified_route is not None
            else await _classify_dispatch_route(user_message)
        )
        if route != "worker_task":
            return False
        is_task = True
        mode = _resolve_worker_delegate_mode(configured_mode, is_task)
    if mode not in {"worker_only", "worker_preferred"}:
        return False

    try:
        from core.heartbeat_store import heartbeat_store
        from core.tool_access_store import tool_access_store
        from core.worker_store import worker_registry

        worker_id = await heartbeat_store.get_active_worker_id(user_id)
        worker = await worker_registry.get_worker(worker_id) if worker_id else None
        if not worker:
            worker = await worker_registry.ensure_default_worker()
            worker_id = str(worker.get("id") or "worker-main")
            await heartbeat_store.set_active_worker_id(user_id, worker_id)

        backend = str(CORE_CHAT_WORKER_BACKEND or "").strip().lower() or "core-agent"
        inferred_shell = _looks_like_shell_command(user_message)
        if inferred_shell:
            backend = "shell"
    except Exception as exc:
        logger.error("Worker delegation error: %s", exc, exc_info=True)
        if mode == "worker_only":
            await ctx.reply(f"❌ Worker 调度失败：{exc}")
            return True
        return False

    worker_id_safe = str(worker.get("id") or worker_id or "worker-main")
    worker_memory_allowed, _worker_memory_detail = tool_access_store.is_tool_allowed(
        runtime_user_id=f"worker::{worker_id_safe}::{user_id}",
        platform="worker_runtime",
        tool_name="open_nodes",
        kind="mcp",
    )
    instruction = str(user_message or "")
    enrich_meta: dict[str, Any] = {
        "worker_has_memory": bool(worker_memory_allowed),
        "dialog_context_included": False,
        "memory_summary_included": False,
        "memory_summary_requested": False,
    }
    if backend != "shell":
        instruction, enrich_meta = await _build_worker_instruction_with_context(
            ctx,
            user_id=user_id,
            user_message=instruction,
            worker_has_memory=bool(worker_memory_allowed),
        )
    preview = _instruction_preview(user_message)
    metadata = {
        "platform": ctx.message.platform,
        "chat_id": str(ctx.message.chat.id),
        "user_id": user_id,
        "delegate_mode": mode,
        "configured_mode": configured_mode,
        "intent_is_task": is_task,
        "intent_confidence": round(intent_conf, 3),
        "intent_reason": intent_reason[:160],
        "dispatch_route": "worker_task",
        "inferred_shell": inferred_shell,
    }
    metadata.update(enrich_meta)

    # Notify immediately so users are never left waiting silently.
    await ctx.reply(
        "🧭 我已把任务派发给 Worker 开始执行。\n"
        f"- worker: `{worker_id_safe}`\n"
        f"- backend: `{backend}`\n"
        f"- 任务: {preview}\n\n"
        f"我会每 {WORKER_PROGRESS_INTERVAL_SEC} 秒同步一次进度，完成后立即回传结果。"
    )

    session = _WorkerDelegateSession(
        user_id=user_id,
        worker_id=worker_id_safe,
        backend=backend,
        instruction_preview=preview,
        started_at=time.time(),
        task=asyncio.current_task(),  # placeholder; replaced right after task creation
    )
    bg_task = asyncio.create_task(
        _run_worker_task_background(
            ctx=ctx,
            user_id=user_id,
            worker_id=worker_id_safe,
            backend=backend,
            instruction=instruction,
            metadata=metadata,
            session=session,
        ),
        name=f"worker-delegate-{user_id}-{int(time.time())}",
    )
    session.task = bg_task
    await _set_worker_session(user_id, session)
    return True


async def _maybe_delegate_after_core_capability_gap(
    ctx: UnifiedContext,
    *,
    user_message: str,
    core_response: str,
) -> bool:
    if not _response_indicates_capability_gap(core_response):
        return False
    is_task, _, _ = await _classify_task_intent(user_message)
    if not is_task:
        return False
    return await _maybe_delegate_chat_to_worker(
        ctx,
        user_message,
        force=True,
        force_reason="core_capability_gap_fallback",
    )


async def handle_ai_chat(ctx: UnifiedContext) -> None:
    """
    处理普通文本消息，使用 Gemini AI 生成回复
    支持引用（回复）包含图片或视频的消息
    """
    user_message = ctx.message.text
    # Legacy fallbacks
    update = ctx.platform_event
    context = ctx.platform_ctx

    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id
    platform_name = ctx.message.platform

    if not user_message:
        return

    # Keep heartbeat proactive delivery target aligned with the latest active chat.
    try:
        from core.heartbeat_store import heartbeat_store

        await heartbeat_store.set_delivery_target(
            str(user_id), str(platform_name), str(chat_id)
        )
    except Exception:
        logger.debug("Failed to update heartbeat delivery target.", exc_info=True)

    # 0. Save user message immediately to ensure persistence even if we return early
    # Note: We save the raw user message here.
    # If using history later, we might want to avoid saving duplicates if we constructed a complex prmopt.
    # But for "chat record", raw input is best.
    await add_message(ctx, user_id, "user", user_message)

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        await ctx.reply(
            f"⛔ 抱歉，您没有使用 AI 对话功能的权限。\n您的 ID 是: `{user_id}`\n\n"
        )
        return

    if await _try_handle_waiting_confirmation(ctx, user_message):
        return

    if await _try_handle_memory_commands(ctx, user_message):
        return

    # 0.5 Fast-track: Detected video URL -> Show Options (Download vs Summarize)
    from utils import extract_video_url

    video_url = extract_video_url(user_message)
    if video_url:
        logger.info(f"Detected video URL: {video_url}, presenting options")

        # Save URL to context for callback access
        if context:
            ctx.user_data["pending_video_url"] = video_url
            logger.info(f"[AIHandler] Set pending_video_url for {user_id}: {video_url}")

        await ctx.reply(
            {
                "text": "🔗 **已识别视频链接**\n\n您可以选择以下操作：",
                "ui": {
                    "actions": [
                        [
                            {
                                "text": "📹 下载视频",
                                "callback_data": "action_download_video",
                            },
                            {
                                "text": "📝 生成摘要",
                                "callback_data": "action_summarize_video",
                            },
                        ]
                    ]
                },
            }
        )
        return

    # 检查是否开启了沉浸式翻译
    settings = await get_user_settings(user_id)
    if settings.get("auto_translate", 0):
        # 检查是否是退出指令
        if user_message.strip().lower() in [
            "/cancel",
            "退出",
            "关闭翻译",
            "退出翻译",
            "cancel",
        ]:
            from repositories import set_translation_mode

            await set_translation_mode(user_id, False)
            await ctx.reply("🚫 已退出沉浸式翻译模式。")
            return

        # 翻译模式开启
        thinking_msg = await ctx.reply("🌍 翻译中...")
        await ctx.send_chat_action(action="typing")

        try:
            system_instruction = prompt_composer.compose_base(
                runtime_user_id=str(user_id),
                tools=[],
                runtime_policy_ctx={"agent_kind": "core-manager", "policy": {"tools": {"allow": [], "deny": []}}},
                mode="translate",
            )
            translation_request = (
                "请执行翻译任务。\n"
                "- 如果输入是中文，翻译成英文。\n"
                "- 如果输入是其他语言，翻译成简体中文。\n"
                "- 只输出译文，不要解释。\n\n"
                f"输入：{user_message}"
            )
            response = await gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=translation_request,
                config={
                    "system_instruction": system_instruction,
                },
            )
            if response.text:
                translation_text = f"🌍 **译文**\n\n{response.text}"
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                await ctx.edit_message(msg_id, translation_text)
                await add_message(ctx, user_id, "model", translation_text)
                # 统计
                await increment_stat(user_id, "translations_count")
            else:
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                await ctx.edit_message(msg_id, "❌ 无法翻译。")
        except Exception as e:
            logger.error(f"Translation error: {e}")
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "❌ 翻译服务出错。")
        return

    dispatch_route, dispatch_conf, dispatch_reason = await _classify_dispatch_route(
        user_message
    )
    memory_snapshot = ""
    if dispatch_route == "manager_memory":
        memory_snapshot = await _fetch_user_memory_snapshot(str(user_id))

    # Worker-first dispatch: core manager schedules user tasks to active/default worker.
    # Fallback behavior is controlled by CORE_CHAT_EXECUTION_MODE.
    if await _maybe_delegate_chat_to_worker(
        ctx,
        user_message,
        preclassified_route=(dispatch_route, dispatch_conf, dispatch_reason),
    ):
        return

    # --- Agent Orchestration ---
    from core.agent_orchestrator import agent_orchestrator

    # 1. 检查是否引用了消息 (Reply Context)
    from .message_utils import process_reply_message

    extra_context = ""
    has_media, reply_extra_context, media_data, mime_type = await process_reply_message(
        ctx
    )

    if reply_extra_context:
        extra_context += reply_extra_context

    # Check if we should abort (e.g. file too big)
    if ctx.message.reply_to_message:
        r = ctx.message.reply_to_message
        is_media = r.type in [MessageType.VIDEO, MessageType.AUDIO, MessageType.VOICE]
        if is_media and not has_media:
            return

    # URL 逻辑已移交给 Agent (skill: web_browser, download_video)
    # 不再进行硬编码预加载或弹窗

    # 随机选择一种"消息已收到"的提示
    RECEIVED_PHRASES = [
        "📨 收到！大脑急速运转中...",
        "⚡ 信号已接收，开始解析...",
        "🍪 Bip Bip! 消息直达核心...",
        "📡 神经连接建立中...",
        "💭 正在调取相关记忆...",
        "🐌 稍微有点堵车，马上就好...",
        "✨ 指令已确认，准备施法...",
    ]

    if not has_media:
        thinking_msg = await ctx.reply(random.choice(RECEIVED_PHRASES))
    else:
        thinking_msg = await ctx.reply("🤔 让我看看引用具体内容...")

    # 3. 构建消息上下文 (History)
    final_user_message = user_message
    if extra_context:
        final_user_message = extra_context + "用户请求：" + user_message
    if memory_snapshot:
        final_user_message = (
            "【已检索到用户记忆】\n"
            f"{memory_snapshot}\n\n"
            "请先基于上述记忆回答用户本人相关问题；如果记忆中没有对应信息，再明确说明未知。\n"
            "回答时优先使用已检索到的事实，不要编造未给出的信息。\n\n"
            f"用户请求：{user_message}"
        )

    # User message already saved at start of function.
    # await add_message(context, user_id, "user", final_user_message)

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    # 动态加载词库
    LOADING_PHRASES = [
        "🤖 调用赛博算力中...",
        "💭 此问题稍显深奥...",
        "🛁 顺手清洗下数据管道...",
        "📡 正在尝试连接火星通讯...",
        "🍪 先给 AI 喂块饼干补充体力...",
        "🐌 稍等，前面有点堵...",
        "📚 翻阅百科全书中...",
        "🔨 正在狂敲代码实现需求...",
        "🌌 试图穿越虫洞寻找答案...",
        "🧹 清理一下内存碎片...",
        "🔌 检查下网线接好没...",
        "🎨 正在为您绘制思维导图...",
        "🍕 吃口披萨，马上回来...",
        "🧘 数字冥想中...",
        "🏃 全力冲刺中...",
    ]

    # 共享状态
    state = {"last_update_time": time.time(), "final_text": "", "running": True}

    async def loading_animation():
        """
        后台动画任务：每隔几秒检查是否有新内容。
        如果卡住了（比如在调用 Tools），通过修改消息来“卖萌”。
        """
        while state["running"]:
            await asyncio.sleep(4)  # Check every 4s
            if not state["running"]:
                break

            now = time.time()
            # 如果超过 5 秒没有更新文本（说明卡在 Tool 或者生成慢）
            if now - state["last_update_time"] > 5:
                phrase = random.choice(LOADING_PHRASES)

                # 如果已经有一部分文本了，附在后面；如果是空的，直接显示
                display_text = state["final_text"]
                if display_text:
                    display_text += f"\n\n⏳ {phrase}"
                else:
                    display_text = phrase

                try:
                    msg_id = getattr(
                        thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                    )
                    await ctx.edit_message(msg_id, display_text)
                except Exception as e:
                    logger.debug(f"Animation edit failed: {e}")

                # Update time to avoid spamming edits (waiting another cycle)
                state["last_update_time"] = time.time()

    # Default to True for backward compatibility or if adapter missing
    can_update = getattr(ctx._adapter, "can_update_message", True)

    # 启动动画任务 (仅当支持消息更新时，也就是非 DingTalk)
    animation_task = None
    if can_update:
        animation_task = asyncio.create_task(loading_animation())

    # --- Task Registration ---
    from core.task_manager import task_manager

    current_task = asyncio.current_task()
    await task_manager.register_task(user_id, current_task, description="AI 对话")

    try:
        message_history = []

        # 构建当前消息
        current_msg_parts = []
        current_msg_parts.append({"text": final_user_message})

        if has_media and media_data:
            current_msg_parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(bytes(media_data)).decode("utf-8"),
                    }
                }
            )

        # 获取历史上下文
        # HACK: Because 'add_message' only saves TEXT to DB, we lose the media info if we just fetch from DB.
        # So we need to:
        # 1. Fetch history from DB (which now includes the latest text-only message)
        # 2. POP the last message from history (which is our text-only version)
        # 3. Append our rich 'current_msg_parts' (with Text + Media)

        history = await get_user_context(ctx, user_id)  # Returns list of dicts

        if history and len(history) > 0 and history[-1]["role"] == "user":
            # Check if the last DB message matches our current text (sanity check)
            last_db_text = history[-1]["parts"][0]["text"]
            if last_db_text == final_user_message:
                # Remove it, so we can replace it with the Rich version
                history.pop()

        # 拼接: History + Current Rich Message
        message_history.extend(history)
        message_history.append({"role": "user", "parts": current_msg_parts})

        # B. 调用 Agent Orchestrator
        final_text_response = ""
        last_stream_update = 0

        async for chunk_text in agent_orchestrator.handle_message(ctx, message_history):
            # 检查任务是否被取消（虽然 await 会抛出 CancelledError，但主动检查更安全）
            if task_manager.is_cancelled(user_id):
                logger.info(f"Task cancelled check hit for user {user_id}")
                raise asyncio.CancelledError()

            final_text_response += chunk_text
            state["final_text"] = final_text_response
            state["last_update_time"] = time.time()

            # Update UI (Standard Stream) - ONLY if supported
            if can_update:
                now = time.time()
                if now - last_stream_update > 1.0:  # Reduce frequency slightly
                    msg_id = getattr(
                        thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                    )
                    try:
                        await ctx.edit_message(msg_id, final_text_response)
                    except MessageSendError as edit_err:
                        # Long stream content is handled by preview-truncation in UnifiedContext;
                        # if platform still rejects, just skip this tick and continue.
                        if not _is_message_too_long_error(edit_err):
                            raise
                    last_stream_update = now

        # 停止动画
        state["running"] = False
        if animation_task:
            animation_task.cancel()  # Ensure it stops immediately

        # 5. 发送最终回复并入库
        if final_text_response:
            delegated = await _maybe_delegate_after_core_capability_gap(
                ctx,
                user_message=user_message,
                core_response=final_text_response,
            )
            if delegated:
                if can_update:
                    try:
                        await thinking_msg.delete()
                    except Exception:
                        logger.debug(
                            "Failed to delete thinking msg before worker fallback delegation.",
                            exc_info=True,
                        )
                return

            rendered_response = await process_and_send_code_files(
                ctx, final_text_response
            )
            # 用户体验优化：为了避免工具产生的中间消息导致最终结果被顶上去需要翻页，
            # 这里改为发送一条新消息作为最终结果，并删除原本的"思考中"消息。

            # 1. 检查是否有 Skill 返回的 UI 组件/按钮
            ui_payload = None
            pending_ui = ctx.user_data.pop("pending_ui", None)
            if pending_ui and isinstance(pending_ui, list):
                actions = []
                for ui_block in pending_ui:
                    if not isinstance(ui_block, dict):
                        continue
                    block_actions = ui_block.get("actions")
                    if isinstance(block_actions, list):
                        actions.extend(block_actions)
                if actions:
                    ui_payload = {"actions": actions}

            # 2. 发送新消息
            try:
                if len(final_text_response) > LONG_RESPONSE_FILE_THRESHOLD:
                    preview_text = rendered_response.strip()
                    if len(preview_text) > 1200:
                        preview_text = (
                            preview_text[:1200].rstrip()
                            + "\n\n...（内容较长，完整结果见附件）"
                        )
                    sent_msg = None
                    if preview_text:
                        payload = {"text": preview_text}
                        if ui_payload:
                            payload["ui"] = ui_payload
                        sent_msg = await ctx.reply(payload)
                    await ctx.reply("📝 内容较长，完整结果已转为 Markdown 文件发送。")
                    sent_msg = await _send_response_as_markdown_file(
                        ctx, final_text_response
                    )
                else:
                    payload = {"text": rendered_response}
                    if ui_payload:
                        payload["ui"] = ui_payload
                    sent_msg = await ctx.reply(payload)
            except MessageSendError as send_err:
                if not _is_message_too_long_error(send_err):
                    raise
                await ctx.reply("⚠️ 文本过长，正在转换为文件发送...")
                sent_msg = await _send_response_as_markdown_file(
                    ctx, final_text_response
                )

            # 2. 尝试删除旧的思考消息 (如果发送成功)
            # 如果支持编辑（Telegram/Discord），尝试删除思考中消息
            # 如果不支持（DingTalk），思考中消息可能会留着，或者尝试删除（返回 False）
            if sent_msg and can_update:
                try:
                    await thinking_msg.delete()
                except Exception as del_e:
                    logger.warning(f"Failed to delete thinking_msg: {del_e}")
            elif not sent_msg and can_update:  # Fallback edit
                # 如果发送失败（极少见），则降级为编辑旧消息
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                sent_msg = await ctx.edit_message(msg_id, rendered_response)

            # 记录模型回复到上下文 (Explicitly save final response)
            await add_message(ctx, user_id, "model", final_text_response)

            # 记录统计
            await increment_stat(user_id, "ai_chats")

    except asyncio.CancelledError:
        logger.info(f"AI chat task cancelled for user {user_id}")
        state["running"] = False
        if animation_task:
            animation_task.cancel()
        # 不发送错误消息，因为 /stop 已经回复了
        raise

    except Exception as e:
        state["running"] = False
        if animation_task:
            animation_task.cancel()
        logger.error(f"Agent error: {e}", exc_info=True)

        if str(e) == "Message is not modified":
            pass
        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(
                msg_id, f"❌ Agent 运行出错：{e}\n\n请尝试 /new 重置对话。"
            )
    finally:
        task_manager.unregister_task(user_id)


async def handle_ai_photo(ctx: UnifiedContext) -> None:
    """
    处理图片消息，使用 Gemini AI 分析图片
    """
    user_id = ctx.message.user.id

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        await ctx.reply(f"⛔ 抱歉，您没有使用 AI 功能的权限。\n您的 ID 是: `{user_id}`")
        return

    try:
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.IMAGE},
            auto_download=True,
        )
    except MediaProcessingError as exc:
        if exc.error_code == "unsupported_media_on_platform":
            await ctx.reply("❌ 当前平台暂不支持该图片消息格式，请改为发送普通图片。")
        else:
            await ctx.reply(
                "❌ 当前平台暂时无法下载图片内容。请稍后重试，或附带文字说明后再发送。"
            )
        return

    if not media.content:
        await ctx.reply("❌ 无法获取图片数据，请重新发送。")
        return

    caption = media.caption or "请描述这张图片"

    # Save to history immediately
    await add_message(ctx, user_id, "user", f"【用户发送了一张图片】 {caption}")

    # 立即发送"正在分析"提示
    thinking_msg = await ctx.reply("🔍 让我仔细看看这张图...")

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    try:
        # 构建带图片的内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": media.mime_type or "image/jpeg",
                            "data": base64.b64encode(bytes(media.content)).decode(
                                "utf-8"
                            ),
                        }
                    },
                ]
            }
        ]

        # 调用 Gemini API
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "system_instruction": prompt_composer.compose_base(
                    runtime_user_id=str(user_id),
                    tools=[],
                    runtime_policy_ctx={"agent_kind": "core-manager", "policy": {"tools": {"allow": [], "deny": []}}},
                    mode="media_image",
                ),
            },
        )

        if response.text:
            # 更新消息
            # 更新消息
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, response.text)

            # Save model response to history
            await add_message(ctx, user_id, "model", response.text)

            # 记录统计
            await increment_stat(user_id, "photo_analyses")

        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "抱歉，我无法分析这张图片。请稍后再试。")

    except Exception as e:
        logger.error(f"AI photo analysis error: {e}")
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        await ctx.edit_message(msg_id, "❌ 图片分析失败，请稍后再试。")


async def handle_ai_video(ctx: UnifiedContext) -> None:
    """
    处理视频消息，使用 Gemini AI 分析视频
    """
    user_id = ctx.message.user.id

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        await ctx.reply(f"⛔ 抱歉，您没有使用 AI 功能的权限。\n您的 ID 是: `{user_id}`")
        return

    try:
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.VIDEO},
            auto_download=True,
        )
    except MediaProcessingError as exc:
        if exc.error_code == "unsupported_media_on_platform":
            await ctx.reply("❌ 当前平台暂不支持该视频消息格式，请改为发送标准视频文件。")
        else:
            await ctx.reply("❌ 当前平台暂时无法下载视频内容，请稍后重试。")
        return

    if not media.content:
        await ctx.reply("❌ 无法获取视频数据，请重新发送。")
        return

    caption = media.caption or "请分析这个视频的内容"

    # Save to history immediately
    await add_message(ctx, user_id, "user", f"【用户发送了一个视频】 {caption}")

    # 检查视频大小（Gemini 有限制）
    # 检查视频大小（Gemini 有限制）
    # 检查视频大小（Gemini 有限制）
    if media.file_size and media.file_size > 20 * 1024 * 1024:  # 20MB 限制
        await ctx.reply(
            "⚠️ 视频文件过大（超过 20MB），无法分析。\n\n请尝试发送较短的视频片段。"
        )
        return

    # 立即发送"正在分析"提示
    thinking_msg = await ctx.reply("🎬 视频分析中，请稍候片刻...")

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    try:
        # 获取 MIME 类型
        mime_type = media.mime_type or "video/mp4"

        # 构建带视频的内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(media.content)).decode(
                                "utf-8"
                            ),
                        }
                    },
                ]
            }
        ]

        # 调用 Gemini API
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "system_instruction": prompt_composer.compose_base(
                    runtime_user_id=str(user_id),
                    tools=[],
                    runtime_policy_ctx={"agent_kind": "core-manager", "policy": {"tools": {"allow": [], "deny": []}}},
                    mode="media_video",
                ),
            },
        )

        if response.text:
            # Update the thinking message with the model response
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, response.text)

            # Save model response to history
            await add_message(ctx, user_id, "model", response.text)

            # 记录统计
            await increment_stat(user_id, "video_analyses")
        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "抱歉，我无法分析这个视频。请稍后再试。")

    except Exception as e:
        logger.error(f"AI video analysis error: {e}")
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        await ctx.edit_message(
            msg_id,
            "❌ 视频分析失败，请稍后再试。\n\n"
            "可能的原因：\n"
            "• 视频格式不支持\n"
            "• 视频时长过长\n"
            "• 服务暂时不可用",
        )


async def handle_sticker_message(ctx: UnifiedContext) -> None:
    """
    处理表情包消息，将其转换为图片进行分析
    """
    user_id = ctx.message.user.id

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        return  # Silent ignore for stickers if unauthorized? Or reply?

    try:
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.STICKER, MessageType.ANIMATION},
            auto_download=True,
        )
    except MediaProcessingError:
        return

    if not media.content:
        return

    # Check if animated or video sticker (might be harder to handle)
    is_animated = bool(media.meta.get("is_animated"))
    is_video = bool(media.meta.get("is_video"))

    caption = "请描述这个表情包的情感和内容"

    # Save to history
    await add_message(ctx, user_id, "user", f"【用户发送了一个表情包】")

    thinking_msg = await ctx.reply("🤔 这个表情包有点意思...")
    await ctx.send_chat_action(action="typing")

    try:
        # Download
        mime_type = media.mime_type or "image/webp"
        if is_animated:
            # TGS format (lottie). API might not support it directly as image.
            # Maybe treat as document? Or skip?
            # Start with supporting static webp and video webm
            pass
        if is_video:
            mime_type = "video/webm"

        # 构建内容
        contents = [
            {
                "parts": [
                    {"text": caption},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(bytes(media.content)).decode("utf-8"),
                        }
                    },
                ]
            }
        ]

        # Call API
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "system_instruction": prompt_composer.compose_base(
                    runtime_user_id=str(user_id),
                    tools=[],
                    runtime_policy_ctx={"agent_kind": "core-manager", "policy": {"tools": {"allow": [], "deny": []}}},
                    mode="media_meme",
                ),
            },
        )

        if response.text:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, response.text)
            await add_message(ctx, user_id, "model", response.text)
            await increment_stat(user_id, "photo_analyses")  # Count as photo
        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "😵 没看懂这个表情包...")

    except Exception as e:
        logger.error(f"Sticker analysis error: {e}")
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        await ctx.edit_message(msg_id, "❌ 表情包分析失败")
