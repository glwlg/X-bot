import time
import asyncio
import logging
import base64
import contextlib
import os
import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any
from core.platform.models import UnifiedContext, MessageType
from core.markdown_memory_store import markdown_memory_store

from core.config import get_client_for_model
from core.file_artifacts import (
    extract_saved_file_rows,
    merge_file_rows,
    normalize_file_rows,
)
from core.model_config import get_current_model, get_vision_model
from core.platform.exceptions import MediaProcessingError, MessageSendError
from core.runtime_callbacks import pop_runtime_callback, set_runtime_callback
from services.openai_adapter import generate_text

from user_context import get_user_context, add_message
from stats import increment_stat
from core.prompt_composer import prompt_composer
from handlers.chat_task_bridge import maybe_bind_recent_followup_context
from .media_utils import extract_media_input
from .message_utils import process_and_send_code_files

logger = logging.getLogger(__name__)

LONG_RESPONSE_FILE_THRESHOLD = 9000

DEFAULT_RECEIVED_PHRASES = [
    "...",
]

DEFAULT_LOADING_PHRASES = [
    ".",
    "..",
    "...",
    "..",
]


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, value)


def _stream_cut_index(text: str, max_chars: int) -> int:
    if not text:
        return 0
    if len(text) <= max_chars:
        return len(text)
    head = text[:max_chars]
    candidates = (
        "\n\n",
        "\n",
        "。",
        "！",
        "？",
        ". ",
        "! ",
        "? ",
        "；",
        ";",
    )
    best = -1
    for marker in candidates:
        idx = head.rfind(marker)
        if idx > best:
            best = idx + len(marker)
    if best >= int(max_chars * 0.35):
        return best
    return max_chars


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


_VERBOSE_PROGRESS_MARKERS = (
    "## ",
    "```",
    "http://",
    "https://",
    "【搜索结果摘要】",
    "当前：",
    "今天（",
    "明天（",
    "天气情况",
    "保存路径",
    "文件路径",
    "{'ok': true",
    "{'ok': True",
)


def _format_manager_progress_summary(
    tool_name: str,
    summary: str,
    *,
    ok: bool,
    history_visibility: str = "",
) -> str:
    raw = str(summary or "").strip()
    if not raw:
        return ""

    if str(history_visibility or "").strip().lower() == "suppress_success" and ok:
        return ""

    normalized_tool = str(tool_name or "").strip().lower()
    if normalized_tool.startswith("ext_"):
        normalized_tool = normalized_tool[4:]

    if normalized_tool == "load_skill":
        return "技能已加载，正在继续执行。"

    if not ok:
        return _compact_text(raw.splitlines()[0].strip(), limit=180)

    if (
        "\n" in raw
        or len(raw) > 120
        or any(marker in raw for marker in _VERBOSE_PROGRESS_MARKERS)
    ):
        return "已完成当前步骤，正在整理结果。"

    return _compact_text(raw, limit=180)


def _humanize_manager_tool_name(tool_name: str) -> str:
    raw = str(tool_name or "").strip().lower()
    if not raw:
        return "工具"
    if raw.startswith("ext_"):
        raw = raw[4:]
    aliases = {
        "bash": "Shell",
        "read": "读取文件",
        "write": "写入文件",
        "edit": "编辑文件",
        "load_skill": "加载技能",
        "codex_session": "编程会话",
        "dispatch_worker": "派发助手",
        "git_ops": "Git 操作",
        "worker_status": "查询状态",
        "list_workers": "列出助手",
        "repo_workspace": "仓库工作区",
        "web_search": "搜索",
        "web_extractor": "网页提取",
    }
    return aliases.get(raw, raw.replace("_", " "))


def _summarize_manager_tool_args(
    tool_name: str, tool_args: dict[str, Any] | None
) -> tuple[str, str]:
    args = dict(tool_args or {})
    raw = str(tool_name or "").strip().lower()

    if raw == "bash":
        command = _compact_text(str(args.get("command") or ""), limit=180)
        return "命令", command

    if raw in {"read", "write", "edit"}:
        for key in ("path", "file_path", "target_file", "target_path"):
            path_text = _compact_text(str(args.get(key) or ""), limit=180)
            if path_text:
                return "路径", path_text

    if raw == "load_skill":
        for key in ("skill_name", "name", "skill"):
            skill_name = _compact_text(str(args.get(key) or ""), limit=120)
            if skill_name:
                return "技能", skill_name

    if raw == "dispatch_worker":
        worker_name = _compact_text(str(args.get("worker_id") or ""), limit=80)
        instruction = _compact_text(str(args.get("instruction") or ""), limit=160)
        if worker_name and instruction:
            return "任务", f"{worker_name}: {instruction}"
        if instruction:
            return "任务", instruction
        if worker_name:
            return "助手", worker_name

    if raw == "worker_status":
        task_id = _compact_text(str(args.get("task_id") or ""), limit=120)
        worker_name = _compact_text(str(args.get("worker_id") or ""), limit=80)
        if task_id:
            return "任务", task_id
        if worker_name:
            return "助手", worker_name

    if raw == "repo_workspace":
        action = _compact_text(str(args.get("action") or ""), limit=80)
        workspace_id = _compact_text(str(args.get("workspace_id") or ""), limit=120)
        if action and workspace_id:
            return "操作", f"{action}: {workspace_id}"
        if action:
            return "操作", action

    if raw == "codex_session":
        action = _compact_text(str(args.get("action") or ""), limit=80)
        session_id = _compact_text(str(args.get("session_id") or ""), limit=120)
        if action and session_id:
            return "会话", f"{action}: {session_id}"
        if action:
            return "操作", action

    if raw == "git_ops":
        action = _compact_text(str(args.get("action") or ""), limit=80)
        workspace_id = _compact_text(str(args.get("workspace_id") or ""), limit=120)
        if action and workspace_id:
            return "操作", f"{action}: {workspace_id}"
        if action:
            return "操作", action

    preferred_keys = (
        "query",
        "url",
        "instruction",
        "task_id",
        "worker_id",
        "name",
    )
    for key in preferred_keys:
        value = _compact_text(str(args.get(key) or ""), limit=180)
        if value:
            return "参数", f"{key}={value}"

    for key, value in args.items():
        rendered = _compact_text(str(value or ""), limit=180)
        if rendered:
            return "参数", f"{key}={rendered}"

    return "", ""


def _build_manager_progress_text(snapshot: dict[str, Any]) -> str:
    payload = dict(snapshot or {})
    event = str(payload.get("event") or "").strip().lower()
    turn = max(0, int(payload.get("turn") or 0))
    task_id = str(payload.get("task_id") or "").strip()
    recent_steps = [
        dict(item)
        for item in list(payload.get("recent_steps") or [])
        if isinstance(item, dict)
    ]
    latest_step = recent_steps[-1] if recent_steps else {}
    tool_name = _humanize_manager_tool_name(
        str(latest_step.get("name") or payload.get("name") or "").strip()
    )
    detail_label = str(latest_step.get("detail_label") or "").strip()
    detail_value = _compact_text(str(latest_step.get("detail") or ""), limit=200)
    summary = _format_manager_progress_summary(
        str(latest_step.get("name") or payload.get("name") or "").strip(),
        str(latest_step.get("summary") or payload.get("summary") or ""),
        ok=(
            str(latest_step.get("status") or "").strip().lower() != "failed"
            and payload.get("ok") is not False
        ),
        history_visibility=str(
            latest_step.get("history_visibility")
            or payload.get("history_visibility")
            or ""
        ),
    )
    failures = [
        _compact_text(str(item or ""), limit=80)
        for item in list(payload.get("failures") or [])
        if str(item or "").strip()
    ]
    final_preview = _compact_text(str(payload.get("final_preview") or ""), limit=160)

    lines = ["⏳ Manager 正在处理请求"]
    if task_id:
        lines.append(f"任务ID：`{task_id}`")
    if turn > 0:
        lines.append(f"回合：{turn}")

    if event == "tool_call_started":
        lines.append(f"动作：开始执行 `{tool_name}`")
    elif event == "tool_call_finished":
        status = str(latest_step.get("status") or "").strip().lower()
        if status == "failed" or payload.get("ok") is False:
            lines.append(f"动作：`{tool_name}` 执行失败")
        else:
            lines.append(f"动作：`{tool_name}` 执行完成")
    elif event == "retry_after_failure":
        lines.append("动作：工具失败后自动重试")
    elif event == "loop_guard":
        lines.append("动作：检测到重复调用，已触发循环保护")
    elif event == "max_turn_limit":
        lines.append("动作：工具回合达到上限，准备收敛结果")
    elif event == "semantic_loop_guard":
        lines.append("动作：检测到语义重复调用，已停止继续搜索")
    elif event == "tool_budget_guard":
        lines.append("动作：单工具调用达到预算上限")
    elif event == "final_response":
        lines.append("动作：正在整理最终回复")
    else:
        lines.append("动作：处理中")

    if detail_label and detail_value:
        lines.append(f"{detail_label}：`{detail_value}`")

    if summary:
        lines.append(f"结果：{summary}")
    elif failures:
        lines.append("最近失败：" + "；".join(failures[:2]))
    elif final_preview:
        lines.append(f"摘要：{final_preview}")

    return "\n".join(lines).strip()


def _build_runtime_phrase_pools(runtime_user_id: str) -> tuple[list[str], list[str]]:
    _ = runtime_user_id
    return list(DEFAULT_RECEIVED_PHRASES), list(DEFAULT_LOADING_PHRASES)


def _pop_pending_ui_payload(user_data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(user_data, dict):
        return None
    pending_ui = user_data.pop("pending_ui", None)
    if not pending_ui:
        return None

    if isinstance(pending_ui, dict):
        actions = pending_ui.get("actions")
        return {"actions": actions} if isinstance(actions, list) and actions else None

    if not isinstance(pending_ui, list):
        return None

    merged_actions: list[Any] = []
    for ui_block in pending_ui:
        if not isinstance(ui_block, dict):
            continue
        block_actions = ui_block.get("actions")
        if isinstance(block_actions, list):
            merged_actions.extend(block_actions)

    if not merged_actions:
        return None
    return {"actions": merged_actions}


async def _send_result_files(
    ctx: UnifiedContext,
    file_rows: list[dict[str, str]],
) -> bool:
    delivered = False
    for item in list(file_rows or []):
        path_text = str(item.get("path") or "").strip()
        if not path_text:
            continue
        path_obj = Path(path_text).expanduser().resolve()
        if not path_obj.exists() or not path_obj.is_file():
            continue
        caption = str(item.get("caption") or "").strip() or None
        filename = str(item.get("filename") or path_obj.name).strip() or path_obj.name
        kind = str(item.get("kind") or "document").strip().lower() or "document"

        try:
            if kind == "photo":
                await ctx.reply_photo(str(path_obj), caption=caption)
            elif kind == "video":
                await ctx.reply_video(str(path_obj), caption=caption)
            elif kind == "audio":
                await ctx.reply_audio(str(path_obj), caption=caption)
            else:
                document: str | bytes = str(path_obj)
                output_name = filename
                if filename.lower().endswith(".md"):
                    try:
                        from services.md_converter import adapt_md_file_for_platform

                        adapted_bytes, adapted_name = adapt_md_file_for_platform(
                            file_bytes=path_obj.read_bytes(),
                            filename=filename,
                            platform=str(getattr(ctx.message, "platform", "") or ""),
                        )
                        document = adapted_bytes
                        output_name = adapted_name
                    except Exception:
                        logger.debug(
                            "Markdown attachment adaptation failed.", exc_info=True
                        )
                await ctx.reply_document(
                    document=document,
                    filename=output_name,
                    caption=caption,
                )
            delivered = True
        except Exception:
            logger.warning(
                "Failed to send result attachment: %s", path_obj, exc_info=True
            )
    return delivered


async def _should_include_memory_summary_for_task(
    user_message: str, dialog_context: str
) -> bool:
    request = str(user_message or "").strip()
    if not request:
        return False
    if len(request) <= 6:
        return True
    joined = f"{request}\n{str(dialog_context or '').strip()}".lower()
    memory_cues = (
        "我",
        "我的",
        "住",
        "城市",
        "地址",
        "偏好",
        "习惯",
        "喜欢",
        "不喜欢",
        "生日",
        "身份",
        "timezone",
        "time zone",
        "where i",
        "my ",
        "preference",
        "profile",
        "remember",
        "记住",
        "记忆",
    )
    task_cues = (
        "部署",
        "deploy",
        "echo ",
        "bash",
        "shell",
        "docker",
        "git ",
        "代码",
        "脚本",
        "测试",
        "test ",
    )
    if any(token in joined for token in memory_cues):
        return True
    if any(token in joined for token in task_cues):
        return False
    return len(request) <= 18


def _is_private_memory_session(ctx: UnifiedContext) -> bool:
    try:
        chat_type = str(getattr(getattr(ctx.message, "chat", None), "type", "") or "")
        normalized = chat_type.strip().lower()
        if normalized:
            if normalized in {"private", "group", "supergroup", "channel"}:
                return normalized == "private"
    except Exception:
        pass
    return True


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
    private_session = _is_private_memory_session(ctx)
    dialog_context = await _collect_recent_dialog_context(
        ctx,
        user_id=user_id,
        current_user_message=user_message,
    )
    wants_memory_summary = (
        private_session
        and await _should_include_memory_summary_for_task(
            user_message,
            dialog_context,
        )
    )
    memory_snapshot = ""
    if wants_memory_summary and not worker_has_memory:
        memory_snapshot = await _fetch_user_memory_snapshot(user_id)

    # SIMPLIFIED: Core Manager no longer micromanages the prompt.
    # The Worker's identity and tools are defined in its SOUL.MD.
    # We only pass the Request and Context.
    sections: list[str] = [
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
        "private_session": private_session,
        "dialog_context_included": bool(dialog_context),
        "memory_summary_included": bool(memory_snapshot),
        "memory_summary_requested": bool(wants_memory_summary),
    }


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


async def _fetch_user_memory_snapshot(user_id: str) -> str:
    try:
        return markdown_memory_store.load_snapshot(
            str(user_id),
            include_daily=True,
            max_chars=2400,
        )
    except Exception:
        return ""


async def _try_handle_waiting_confirmation(
    ctx: UnifiedContext, user_message: str
) -> bool:
    text = (user_message or "").strip().lower()
    if not text:
        return False

    stop_cues = {"停止", "取消", "停止任务", "stop", "cancel"}
    intent_stop = text in stop_cues

    from core.heartbeat_store import heartbeat_store
    from manager.relay.closure_service import manager_closure_service

    user_id = str(ctx.message.user.id)
    active_task = await heartbeat_store.get_session_active_task(user_id)
    if not active_task or active_task.get("status") != "waiting_user":
        return False

    task_id = str(active_task.get("id"))
    if intent_stop:
        await heartbeat_store.update_session_active_task(
            user_id,
            status="cancelled",
            needs_confirmation=False,
            confirmation_deadline="",
            clear_active=True,
            result_summary="Cancelled by user confirmation text.",
        )
        await heartbeat_store.release_lock(user_id)
        await heartbeat_store.append_session_event(
            user_id, f"user_stop_by_text:{task_id}"
        )
        await ctx.reply("🛑 已停止该任务。")
        return True

    resume = await manager_closure_service.resume_waiting_task(
        user_id=user_id,
        user_message=user_message,
        source="text",
    )
    if bool(resume.get("ok")):
        await ctx.reply(str(resume.get("message") or "✅ 已继续当前任务。"))
        return True

    await ctx.reply(
        str(
            resume.get("message")
            or "⚠️ 当前任务暂时无法继续，请稍后重试或重新下达任务。"
        )
    )
    return True


async def _try_handle_memory_commands(ctx: UnifiedContext, user_message: str) -> bool:
    text = str(user_message or "").strip()
    if not text:
        return False
    user_id = str(ctx.message.user.id)
    private_session = _is_private_memory_session(ctx)

    explicit_patterns = (
        r"^(?:请记住|记住|记一下)\s*[:：]?\s*(.+)$",
        r"^remember\s+(.*)$",
    )

    async def _write_user_memory(content: str) -> tuple[bool, str]:
        return markdown_memory_store.remember(
            user_id,
            content,
            source="user_explicit",
        )

    if text.lower() in {"memory list", "memory user", "查看记忆", "我的记忆"}:
        if not private_session:
            await ctx.reply("⚠️ 群聊场景不展示个人 MEMORY.md。请在私聊中使用。")
            return True
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
        if not private_session:
            await ctx.reply("⚠️ 仅支持在私聊中写入个人 MEMORY.md。")
            return True
        content = str(match.group(1) or "").strip()
        if not content:
            break
        ok, detail = await _write_user_memory(content)
        if ok:
            await ctx.reply(f"🧠 已写入 MEMORY.md。\n- 提取到：{detail}")
        else:
            await ctx.reply(f"⚠️ 写入记忆失败：{detail}")
        return True

    return False


async def _maybe_bind_recent_followup_context(
    ctx: UnifiedContext,
    user_message: str,
) -> str:
    return await maybe_bind_recent_followup_context(ctx, user_message)


async def handle_ai_chat(ctx: UnifiedContext) -> None:
    """
    处理普通文本消息，使用对话模型生成回复
    支持引用（回复）包含图片或视频的消息
    """
    user_message = ctx.message.text
    # Legacy fallbacks
    context = ctx.platform_ctx

    chat_id = ctx.message.chat.id
    user_id = ctx.message.user.id
    platform_name = ctx.message.platform

    if not user_message:
        return

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        logger.info("Ignoring unauthorized AI chat from user_id=%s", user_id)
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

    memory_snapshot = ""

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

    followup_context = await _maybe_bind_recent_followup_context(ctx, user_message)

    # URL 逻辑已移交给 Agent (skill: web_browser, download_video)
    # 不再进行硬编码预加载或弹窗

    received_phrases, loading_phrases = _build_runtime_phrase_pools(str(user_id))

    if not has_media:
        thinking_msg = await ctx.reply(received_phrases[0])
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
    if followup_context:
        final_user_message = (
            f"{followup_context}\n\n当前用户补充：{final_user_message}"
        ).strip()

    # User message already saved at start of function.
    # await add_message(context, user_id, "user", final_user_message)

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    # 共享状态
    state = {
        "last_update_time": time.time(),
        "final_text": "",
        "running": True,
        "manager_progress_text": "",
        "manager_progress_task_id": "",
        "manager_progress_draft_id": 0,
        "manager_progress_last_sent_at": 0.0,
        "manager_progress_last_rendered": "",
        "manager_progress_final_preview": "",
        "loading_frame_index": 0,
    }
    manager_progress_steps: list[dict[str, Any]] = []
    pending_manager_files: list[dict[str, str]] = []
    manager_progress_event_names = {
        "tool_call_started",
        "tool_call_finished",
        "retry_after_failure",
        "loop_guard",
        "max_turn_limit",
        "semantic_loop_guard",
        "tool_budget_guard",
        "final_response",
    }
    manager_progress_thread_id = None
    raw_message_data = getattr(ctx.message, "raw_data", {}) or {}
    if isinstance(raw_message_data, dict):
        thread_candidate = raw_message_data.get("message_thread_id")
        if thread_candidate not in (None, ""):
            with contextlib.suppress(Exception):
                manager_progress_thread_id = int(thread_candidate)

    async def loading_animation():
        """
        后台动画任务：如果迟迟没有首个可见结果，用省略号做轻量占位。
        """
        while state["running"]:
            await asyncio.sleep(1.2)
            if not state["running"]:
                break

            now = time.time()
            if now - state["last_update_time"] > 2.5:
                manager_progress_text = str(
                    state.get("manager_progress_text") or ""
                ).strip()
                if manager_progress_text and not state["final_text"]:
                    try:
                        await _push_manager_progress_update(force=False)
                    except Exception as e:
                        logger.debug(f"Manager progress update failed: {e}")
                    continue

                if state["final_text"]:
                    continue

                frame_index = int(state.get("loading_frame_index") or 0)
                display_text = loading_phrases[frame_index % len(loading_phrases)]
                state["loading_frame_index"] = frame_index + 1

                try:
                    msg_id = getattr(
                        thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                    )
                    await ctx.edit_message(msg_id, display_text)
                except Exception as e:
                    logger.debug(f"Animation edit failed: {e}")

    # Default to True for backward compatibility or if adapter missing
    can_update = getattr(ctx._adapter, "can_update_message", True)
    stream_segment_enabled = (
        os.getenv("AI_SEGMENT_STREAM_ENABLED", "true").lower() == "true"
        and str(platform_name or "").lower() in {"telegram", "discord"}
        and not has_media
    )
    stream_min_chars = _env_int("AI_SEGMENT_STREAM_MIN_CHARS", 220, 40)
    stream_max_chars = _env_int("AI_SEGMENT_STREAM_MAX_CHARS", 1200, 160)
    stream_flush_sec = _env_float("AI_SEGMENT_STREAM_FLUSH_SEC", 1.0, 0.2)
    stream_buffer = ""
    stream_chunks_seen = 0
    stream_chunks_sent = 0
    stream_last_sent_ts = 0.0
    stream_locked = False
    thinking_deleted = False

    async def _push_manager_progress_update(*, force: bool) -> None:
        progress_text = str(state.get("manager_progress_text") or "").strip()
        if not progress_text:
            return
        if state["final_text"] and not force:
            return

        now = time.time()
        if (
            not force
            and progress_text == str(state.get("manager_progress_last_rendered") or "")
            and now - float(state.get("manager_progress_last_sent_at") or 0.0) < 3.0
        ):
            return

        adapter = getattr(ctx, "_adapter", None)
        send_draft = getattr(adapter, "send_message_draft", None)
        if callable(send_draft) and str(platform_name or "").lower() == "telegram":
            task_id = str(state.get("manager_progress_task_id") or "").strip()
            draft_id = int(state.get("manager_progress_draft_id") or 0)
            if not draft_id:
                seed = (
                    task_id or f"{chat_id}:{user_id}:{getattr(thinking_msg, 'id', '')}"
                )
                draft_id = max(1, zlib.crc32(seed.encode("utf-8")) & 0x7FFFFFFF)
                state["manager_progress_draft_id"] = draft_id
            await send_draft(
                chat_id=chat_id,
                draft_id=draft_id,
                text=progress_text,
                message_thread_id=manager_progress_thread_id,
                fallback_to_message=False,
            )
            state["manager_progress_last_rendered"] = progress_text
            state["manager_progress_last_sent_at"] = now
            return

        if can_update and not thinking_deleted:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, progress_text)
            state["manager_progress_last_rendered"] = progress_text
            state["manager_progress_last_sent_at"] = now

    async def _manager_progress_callback(snapshot: dict[str, Any]) -> None:
        payload = dict(snapshot or {})
        event_name = str(payload.get("event") or "").strip().lower()
        if not event_name:
            return

        turn = max(0, int(payload.get("turn") or 0))
        task_id_text = str(payload.get("task_id") or "").strip()
        if task_id_text:
            state["manager_progress_task_id"] = task_id_text
            if not int(state.get("manager_progress_draft_id") or 0):
                state["manager_progress_draft_id"] = max(
                    1, zlib.crc32(task_id_text.encode("utf-8")) & 0x7FFFFFFF
                )

        if event_name == "turn_start":
            state["last_update_time"] = time.time()
            return

        if event_name == "tool_call_started":
            detail_label, detail_value = _summarize_manager_tool_args(
                str(payload.get("name") or ""),
                payload.get("args") if isinstance(payload.get("args"), dict) else {},
            )
            manager_progress_steps.append(
                {
                    "name": str(payload.get("name") or "").strip(),
                    "status": "running",
                    "summary": "",
                    "detail_label": detail_label,
                    "detail": detail_value,
                    "turn": turn,
                }
            )
        elif event_name == "tool_call_finished":
            tool_name = str(payload.get("name") or "").strip()
            tool_ok = bool(payload.get("ok"))
            summary = str(payload.get("summary") or "").strip()
            history_visibility = str(payload.get("history_visibility") or "").strip()
            terminal_payload = payload.get("terminal_payload")
            if isinstance(terminal_payload, dict):
                pending_manager_files[:] = merge_file_rows(
                    pending_manager_files,
                    normalize_file_rows(terminal_payload.get("files")),
                    extract_saved_file_rows(
                        str(terminal_payload.get("text") or "").strip()
                    ),
                )
            updated = False
            for idx in range(len(manager_progress_steps) - 1, -1, -1):
                row = manager_progress_steps[idx]
                if str(row.get("name") or "") != tool_name:
                    continue
                if str(row.get("status") or "") != "running":
                    continue
                if int(row.get("turn") or 0) not in {0, turn}:
                    continue
                row["status"] = "done" if tool_ok else "failed"
                row["summary"] = summary[:180]
                row["history_visibility"] = history_visibility
                row["turn"] = turn
                updated = True
                break
            if not updated and tool_name:
                manager_progress_steps.append(
                    {
                        "name": tool_name,
                        "status": "done" if tool_ok else "failed",
                        "summary": summary[:180],
                        "history_visibility": history_visibility,
                        "detail_label": "",
                        "detail": "",
                        "turn": turn,
                    }
                )
        elif event_name == "final_response":
            state["manager_progress_final_preview"] = str(
                payload.get("text_preview") or ""
            )[:180]

        manager_progress_steps[:] = manager_progress_steps[-20:]
        progress_snapshot = dict(payload)
        progress_snapshot["recent_steps"] = manager_progress_steps[-6:]
        progress_snapshot["final_preview"] = str(
            state.get("manager_progress_final_preview") or ""
        )[:180]
        state["manager_progress_text"] = _build_manager_progress_text(progress_snapshot)
        state["last_update_time"] = time.time()

        if event_name in manager_progress_event_names:
            await _push_manager_progress_update(force=True)

    async def _flush_stream_buffer(*, force: bool = False) -> None:
        nonlocal \
            stream_buffer, \
            stream_chunks_sent, \
            stream_last_sent_ts, \
            thinking_deleted
        if not stream_segment_enabled or stream_locked:
            return
        if not stream_buffer:
            return
        now = time.time()
        if not force and now - stream_last_sent_ts < stream_flush_sec:
            return

        while stream_buffer:
            cut = _stream_cut_index(stream_buffer, stream_max_chars)
            if cut <= 0:
                return
            if (
                not force
                and cut < stream_min_chars
                and len(stream_buffer) < stream_max_chars
            ):
                return
            segment = stream_buffer[:cut].strip()
            stream_buffer = stream_buffer[cut:].lstrip()
            if not segment:
                continue
            await ctx.reply(segment)
            stream_chunks_sent += 1
            stream_last_sent_ts = time.time()
            if can_update and not thinking_deleted:
                try:
                    await thinking_msg.delete()
                    thinking_deleted = True
                except Exception:
                    pass
            if not force:
                return

    # 启动动画任务 (仅当支持消息更新时，也就是非 DingTalk)
    animation_task = None
    if can_update:
        animation_task = asyncio.create_task(loading_animation())

    # --- Task Registration ---
    from core.task_manager import task_manager

    current_task = asyncio.current_task()
    await task_manager.register_task(user_id, current_task, description="AI 对话")
    set_runtime_callback(ctx, "manager_progress_callback", _manager_progress_callback)

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
            if last_db_text in {user_message, final_user_message}:
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

            chunk_text = str(chunk_text or "")
            final_text_response += chunk_text
            state["final_text"] = final_text_response
            state["last_update_time"] = time.time()

            stream_chunks_seen += 1
            if stream_segment_enabled:
                if "```" in chunk_text and stream_chunks_sent == 0:
                    stream_locked = True
                if not stream_locked:
                    stream_buffer += chunk_text
                    if stream_chunks_seen >= 2:
                        await _flush_stream_buffer(force=False)

            # Update UI (Standard Stream) - ONLY if supported
            if can_update and (stream_chunks_sent == 0 or stream_locked):
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
            ui_payload = _pop_pending_ui_payload(ctx.user_data)
            pending_result_files = merge_file_rows(
                pending_manager_files,
                extract_saved_file_rows(final_text_response),
            )
            streamed_delivery = (
                stream_segment_enabled
                and stream_chunks_sent > 0
                and not stream_locked
                and not ui_payload
            )

            if streamed_delivery:
                await _flush_stream_buffer(force=True)
                tail = stream_buffer.strip()
                if tail:
                    await ctx.reply(tail)
                if can_update and not thinking_deleted:
                    try:
                        await thinking_msg.delete()
                    except Exception as del_e:
                        logger.warning(f"Failed to delete thinking_msg: {del_e}")
            else:
                rendered_response = await process_and_send_code_files(
                    ctx, final_text_response
                )

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
                        await ctx.reply(
                            "📝 内容较长，完整结果已转为 Markdown 文件发送。"
                        )
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

                if sent_msg and can_update:
                    try:
                        await thinking_msg.delete()
                    except Exception as del_e:
                        logger.warning(f"Failed to delete thinking_msg: {del_e}")
                elif not sent_msg and can_update:
                    msg_id = getattr(
                        thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                    )
                    sent_msg = await ctx.edit_message(msg_id, rendered_response)

            if pending_result_files:
                await _send_result_files(ctx, pending_result_files)

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
        pop_runtime_callback(ctx, "manager_progress_callback")
        task_manager.unregister_task(user_id)


async def handle_ai_photo(ctx: UnifiedContext) -> None:
    """
    处理图片消息，使用对话模型分析图片
    """
    user_id = ctx.message.user.id

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        logger.info("Ignoring unauthorized image message from user_id=%s", user_id)
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

    caption = media.caption or "请分析这张图片"
    history_text = f"【用户发送了一张图片】 {caption}"
    await add_message(ctx, user_id, "user", history_text)

    thinking_msg = await ctx.reply("🔍 让我仔细看看这张图...")
    await ctx.send_chat_action(action="typing")

    from core.agent_orchestrator import agent_orchestrator
    from core.task_manager import task_manager

    current_task = asyncio.current_task()
    await task_manager.register_task(user_id, current_task, description="AI 图片分析")

    try:
        history = await get_user_context(ctx, user_id)
        if history and history[-1].get("role") == "user":
            last_parts = history[-1].get("parts") or []
            last_db_text = ""
            if last_parts and isinstance(last_parts[0], dict):
                last_db_text = str(last_parts[0].get("text") or "")
            if last_db_text == history_text:
                history.pop()

        message_history = list(history)
        message_history.append(
            {
                "role": "user",
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
                ],
            }
        )

        final_text_response = ""
        last_stream_update = 0.0

        async for chunk_text in agent_orchestrator.handle_message(ctx, message_history):
            if task_manager.is_cancelled(user_id):
                raise asyncio.CancelledError()

            piece = str(chunk_text or "")
            if not piece:
                continue

            final_text_response += piece
            now = time.time()
            if now - last_stream_update > 1.0:
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                try:
                    await ctx.edit_message(msg_id, final_text_response)
                except MessageSendError as edit_err:
                    if not _is_message_too_long_error(edit_err):
                        raise
                last_stream_update = now

        if final_text_response:
            ui_payload = _pop_pending_ui_payload(ctx.user_data)
            pending_result_files = extract_saved_file_rows(final_text_response)
            rendered_response = await process_and_send_code_files(
                ctx, final_text_response
            )
            sent_msg = None

            try:
                if len(final_text_response) > LONG_RESPONSE_FILE_THRESHOLD:
                    preview_text = rendered_response.strip()
                    if len(preview_text) > 1200:
                        preview_text = (
                            preview_text[:1200].rstrip()
                            + "\n\n...（内容较长，完整结果见附件）"
                        )
                    if preview_text:
                        payload = {"text": preview_text}
                        if ui_payload:
                            payload["ui"] = ui_payload
                        sent_msg = await ctx.reply(payload)
                    await ctx.reply("📝 内容较长，完整结果已转为 Markdown 文件发送。")
                    sent_msg = await _send_response_as_markdown_file(
                        ctx, final_text_response, prefix="photo_response"
                    )
                elif ui_payload:
                    sent_msg = await ctx.reply(
                        {
                            "text": rendered_response,
                            "ui": ui_payload,
                        }
                    )
                else:
                    msg_id = getattr(
                        thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                    )
                    await ctx.edit_message(msg_id, rendered_response)
                    sent_msg = thinking_msg
            except MessageSendError as send_err:
                if not _is_message_too_long_error(send_err):
                    raise
                await ctx.reply("⚠️ 文本过长，正在转换为文件发送...")
                sent_msg = await _send_response_as_markdown_file(
                    ctx, final_text_response, prefix="photo_response"
                )

            if sent_msg is not thinking_msg:
                try:
                    await thinking_msg.delete()
                except Exception as del_e:
                    logger.warning(f"Failed to delete thinking_msg: {del_e}")

            if pending_result_files:
                await _send_result_files(ctx, pending_result_files)

            await add_message(ctx, user_id, "model", final_text_response)
            await increment_stat(user_id, "photo_analyses")
        else:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "抱歉，我无法分析这张图片。请稍后再试。")

    except asyncio.CancelledError:
        logger.info(f"AI photo analysis task cancelled for user {user_id}")
        raise
    except Exception as e:
        logger.error(f"AI photo analysis error: {e}", exc_info=True)
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        await ctx.edit_message(msg_id, "❌ 图片分析失败，请稍后再试。")
    finally:
        task_manager.unregister_task(user_id)


async def handle_ai_video(ctx: UnifiedContext) -> None:
    """
    处理视频消息，使用对话模型分析视频
    """
    user_id = ctx.message.user.id

    # 检查用户权限
    from core.config import is_user_allowed

    if not await is_user_allowed(user_id):
        logger.info("Ignoring unauthorized video message from user_id=%s", user_id)
        return

    try:
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.VIDEO},
            auto_download=True,
        )
    except MediaProcessingError as exc:
        if exc.error_code == "unsupported_media_on_platform":
            await ctx.reply(
                "❌ 当前平台暂不支持该视频消息格式，请改为发送标准视频文件。"
            )
        else:
            await ctx.reply("❌ 当前平台暂时无法下载视频内容，请稍后重试。")
        return

    if not media.content:
        await ctx.reply("❌ 无法获取视频数据，请重新发送。")
        return

    caption = media.caption or "请分析这个视频的内容"

    # Save to history immediately
    await add_message(ctx, user_id, "user", f"【用户发送了一个视频】 {caption}")

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

        model_to_use = get_vision_model() or get_current_model()
        client_to_use = get_client_for_model(model_to_use, is_async=True)
        if client_to_use is None:
            raise RuntimeError("OpenAI async client is not initialized")
        analysis = await generate_text(
            async_client=client_to_use,
            model=model_to_use,
            contents=contents,
            config={
                "system_instruction": prompt_composer.compose_base(
                    runtime_user_id=str(user_id),
                    platform=str(getattr(ctx.message, "platform", "") or ""),
                    tools=[],
                    runtime_policy_ctx={
                        "agent_kind": "core-manager",
                        "policy": {"tools": {"allow": [], "deny": []}},
                    },
                    mode="media_video",
                )
            },
        )
        analysis = str(analysis or "").strip()

        if analysis:
            # Update the thinking message with the model response
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, analysis)

            # Save model response to history
            await add_message(ctx, user_id, "model", analysis)

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
        logger.info("Ignoring unauthorized sticker message from user_id=%s", user_id)
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
    await add_message(ctx, user_id, "user", "【用户发送了一个表情包】")

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
                            "data": base64.b64encode(bytes(media.content)).decode(
                                "utf-8"
                            ),
                        }
                    },
                ]
            }
        ]

        model_to_use = get_vision_model() or get_current_model()
        client_to_use = get_client_for_model(model_to_use, is_async=True)
        if client_to_use is None:
            raise RuntimeError("OpenAI async client is not initialized")
        analysis = await generate_text(
            async_client=client_to_use,
            model=model_to_use,
            contents=contents,
            config={
                "system_instruction": prompt_composer.compose_base(
                    runtime_user_id=str(user_id),
                    platform=str(getattr(ctx.message, "platform", "") or ""),
                    tools=[],
                    runtime_policy_ctx={
                        "agent_kind": "core-manager",
                        "policy": {"tools": {"allow": [], "deny": []}},
                    },
                    mode="media_meme",
                )
            },
        )
        analysis = str(analysis or "").strip()

        if analysis:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, analysis)
            await add_message(ctx, user_id, "model", analysis)
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
