import time
import asyncio
import logging
import base64
import os
import re
from datetime import datetime
from typing import Any
from core.platform.models import UnifiedContext, MessageType
import random
from core.markdown_memory_store import markdown_memory_store
from core.waiting_phrase_store import waiting_phrase_store

from core.config import get_client_for_model
from core.model_config import get_current_model, get_image_model
from core.platform.exceptions import MediaProcessingError, MessageSendError
from services.openai_adapter import generate_text

from user_context import get_user_context, add_message
from core.state_store import get_user_settings
from stats import increment_stat
from core.prompt_composer import prompt_composer
from .media_utils import extract_media_input
from .message_utils import process_and_send_code_files

logger = logging.getLogger(__name__)

LONG_RESPONSE_FILE_THRESHOLD = 9000

DEFAULT_RECEIVED_PHRASES = [
    "📨 收到！大脑急速运转中...",
    "⚡ 信号已接收，开始解析...",
    "🍪 Bip Bip! 消息直达核心...",
    "📡 神经连接建立中...",
    "💭 正在调取相关记忆...",
    "🐌 稍微有点堵车，马上就好...",
    "✨ 指令已确认，准备施法...",
]

DEFAULT_LOADING_PHRASES = [
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


def _normalize_phrase_pool(items: list[str], *, limit: int = 24) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in items:
        phrase = " ".join(str(raw or "").split()).strip().strip("`*")
        if not phrase:
            continue
        if phrase in seen:
            continue
        seen.add(phrase)
        normalized.append(phrase)
        if len(normalized) >= max(1, int(limit)):
            break
    return normalized


def _build_runtime_phrase_pools(runtime_user_id: str) -> tuple[list[str], list[str]]:
    fallback_received = list(DEFAULT_RECEIVED_PHRASES)
    fallback_loading = list(DEFAULT_LOADING_PHRASES)
    try:
        pools = waiting_phrase_store.load_phrase_pools_for_runtime_user(
            str(runtime_user_id)
        )
        if not pools:
            return fallback_received, fallback_loading

        received, loading = pools
        normalized_received = _normalize_phrase_pool(received, limit=24)
        normalized_loading = _normalize_phrase_pool(loading, limit=24)
        if not normalized_received or not normalized_loading:
            return fallback_received, fallback_loading
        return normalized_received, normalized_loading
    except Exception as exc:
        logger.debug("Failed to build dynamic phrase pools from SOUL.MD: %s", exc)
        return fallback_received, fallback_loading


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
        await heartbeat_store.append_session_event(
            user_id, f"user_continue_by_text:{task_id}"
        )
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
        await heartbeat_store.append_session_event(
            user_id, f"user_stop_by_text:{task_id}"
        )
        await ctx.reply("🛑 已停止该任务。")
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
            from core.state_store import set_translation_mode

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
                runtime_policy_ctx={
                    "agent_kind": "core-manager",
                    "policy": {"tools": {"allow": [], "deny": []}},
                },
                mode="translate",
            )
            translation_request = (
                "请执行翻译任务。\n"
                "- 如果输入是中文，翻译成英文。\n"
                "- 如果输入是其他语言，翻译成简体中文。\n"
                "- 只输出译文，不要解释。\n\n"
                f"输入：{user_message}"
            )
            model_to_use = get_current_model()
            client_to_use = get_client_for_model(model_to_use, is_async=True)
            if client_to_use is None:
                raise RuntimeError("OpenAI async client is not initialized")
            translated = await generate_text(
                async_client=client_to_use,
                model=model_to_use,
                contents=translation_request,
                config={"system_instruction": system_instruction},
            )
            translated = str(translated or "").strip()
            if translated:
                translation_text = f"🌍 **译文**\n\n{translated}"
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

    # URL 逻辑已移交给 Agent (skill: web_browser, download_video)
    # 不再进行硬编码预加载或弹窗

    received_phrases, loading_phrases = _build_runtime_phrase_pools(str(user_id))

    if not has_media:
        thinking_msg = await ctx.reply(random.choice(received_phrases))
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
                phrase = random.choice(loading_phrases)

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
    处理图片消息，使用对话模型分析图片
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

        model_to_use = get_image_model() or get_current_model()
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

        model_to_use = get_image_model() or get_current_model()
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
