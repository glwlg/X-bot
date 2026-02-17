"""
语音消息处理模块 - 智能路由版

短语音（≤60s）: 转文字后走智能路由（与文本消息一致）
长语音（>60s）: 直接转写输出
"""

import logging
import base64
import re
from telegram.error import BadRequest

from core.config import gemini_client, GEMINI_MODEL, is_user_allowed
from core.platform.exceptions import MediaProcessingError
from user_context import add_message, get_user_context
from core.platform.models import MessageType, UnifiedContext
from .media_utils import extract_media_input

logger = logging.getLogger(__name__)

# 语音时长阈值（秒）
SHORT_VOICE_THRESHOLD = 60


def _normalize_transcribed_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""

    # Remove common wrapper labels.
    for prefix in ("转写：", "转写结果：", "识别结果：", "文本："):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()

    # Strip symmetrical quote wrappers repeatedly.
    pairs = (
        ('"', '"'),
        ("'", "'"),
        ("`", "`"),
        ("“", "”"),
        ("‘", "’"),
    )
    changed = True
    while changed and len(text) >= 2:
        changed = False
        for left, right in pairs:
            if text.startswith(left) and text.endswith(right):
                text = text[len(left) : len(text) - len(right)].strip()
                changed = True
                break

    # Quote/punctuation only output means model produced no usable transcript.
    if re.fullmatch(r'[\s"`\'“”‘’.,，。!?！？:：;；\-\(\)\[\]\{\}…]+', text or ""):
        return ""
    return text


def _extract_model_text(response) -> str:
    if response is None:
        return ""

    try:
        direct_text = getattr(response, "text", None)
    except Exception:
        direct_text = None
    if direct_text is not None:
        text = str(direct_text).strip()
        if text:
            return text

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        chunks = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))
        merged = "\n".join(chunks).strip()
        if merged:
            return merged
    return ""


def _audio_mime_candidates(mime_type: str) -> list[str]:
    raw = str(mime_type or "").strip()
    base = raw.split(";", 1)[0].strip().lower() if raw else ""
    candidates: list[str] = []

    def add(item: str) -> None:
        value = str(item or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    add(raw)
    add(base)

    if base in {"audio/ogg", "audio/opus", "audio/x-opus", "application/ogg"}:
        add("audio/ogg")
        add("audio/ogg; codecs=opus")
        add("audio/opus")
    if base in {"audio/mp3", "audio/mpeg"}:
        add("audio/mpeg")
        add("audio/mp3")

    add("audio/ogg")
    add("audio/ogg; codecs=opus")
    add("audio/webm")
    add("audio/mpeg")
    add("audio/mp4")
    add("audio/wav")
    return candidates


def _build_audio_contents(
    prompt: str, voice_bytes: bytes, mime_type: str
) -> list[dict]:
    return [
        {
            "role": "user",
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(bytes(voice_bytes)).decode("utf-8"),
                    }
                },
            ],
        }
    ]


async def _run_audio_prompt(prompt: str, voice_bytes: bytes, mime_type: str) -> str:
    last_error: Exception | None = None
    for candidate_mime in _audio_mime_candidates(mime_type):
        try:
            response = await gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=_build_audio_contents(prompt, voice_bytes, candidate_mime),
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Voice model call failed with mime=%s err=%s",
                candidate_mime,
                exc,
            )
            continue

        text = _extract_model_text(response)
        if text:
            return text

    if last_error is not None:
        logger.error("Voice model call failed after mime retries: %s", last_error)
    return ""


async def transcribe_voice(voice_bytes: bytes, mime_type: str) -> str | None:
    """
    使用 Gemini 转写语音为文字

    Returns:
        转写后的文本，失败返回 None
    """
    if not voice_bytes:
        logger.warning("Voice transcription skipped: empty audio payload.")
        return None

    try:
        prompt = (
            "请将这段语音转写为文字。"
            "只输出语音中说的原话，不要添加任何解释或回复。"
            "如果无法识别，返回空字符串。"
        )
        text = _normalize_transcribed_text(
            await _run_audio_prompt(prompt, voice_bytes, mime_type)
        )
        if text:
            return text

        # Retry once with a stricter instruction to avoid placeholder outputs like """".
        strict_prompt = (
            "请将这段语音准确转写为文字。"
            "只输出原话，不要输出引号、占位符或解释。"
            "如果听不清，必须返回空字符串。"
        )
        retry_text = _normalize_transcribed_text(
            await _run_audio_prompt(strict_prompt, voice_bytes, mime_type)
        )
        if retry_text:
            return retry_text
        return None
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        return None


async def transcribe_and_translate_voice(
    voice_bytes: bytes, mime_type: str
) -> dict | None:
    """
    转写语音并翻译为双语对照

    Returns:
        {"original": "原文", "original_lang": "语言", "translated": "译文"} 或 None
    """
    if not voice_bytes:
        logger.warning("Voice translation skipped: empty audio payload.")
        return None

    try:
        prompt = (
            "请完成以下任务：\n"
            "1. 将语音转写为文字\n"
            "2. 识别语音的语言\n"
            "3. 如果是中文，翻译为英文；如果是其他语言，翻译为中文\n\n"
            "请严格按以下格式输出（不要添加其他内容）：\n"
            "原文语言：[语言名称]\n"
            "原文：[转写的原文]\n"
            "译文：[翻译后的文字]"
        )

        text = await _run_audio_prompt(prompt, voice_bytes, mime_type)
        if not text:
            return None

        # 解析结果
        text = text.strip()
        result = {}

        for line in text.split("\n"):
            if line.startswith("原文语言："):
                result["original_lang"] = line.replace("原文语言：", "").strip()
            elif line.startswith("原文："):
                result["original"] = line.replace("原文：", "").strip()
            elif line.startswith("译文："):
                result["translated"] = line.replace("译文：", "").strip()

        if result.get("original") and result.get("translated"):
            return result
        return None

    except Exception as e:
        logger.error(f"Voice translation error: {e}")
        return None


async def handle_voice_message(ctx: UnifiedContext) -> None:
    """
    处理语音消息（包括 voice 和 audio 类型）

    翻译模式开启: 转写 + 翻译 → 双语对照输出
    正常模式:
        短语音: 转文字 → 智能路由
        长语音: 直接转写输出
    """
    from core.state_store import get_user_settings

    user_id = ctx.message.user.id

    # 检查用户权限
    if not await is_user_allowed(user_id):
        await ctx.reply("⛔ 抱歉，您没有使用 AI 功能的权限。")
        return

    try:
        media = await extract_media_input(
            ctx,
            expected_types={MessageType.VOICE, MessageType.AUDIO},
            auto_download=True,
        )
    except MediaProcessingError as exc:
        if exc.error_code == "unsupported_media_on_platform":
            await ctx.reply("❌ 当前平台暂不支持该语音/音频格式。")
        else:
            await ctx.reply("❌ 当前平台暂时无法下载语音/音频内容，请稍后再试。")
        return

    mime_type = media.mime_type or "audio/ogg"
    duration = int(media.meta.get("duration") or (SHORT_VOICE_THRESHOLD + 1))
    user_instruction = (
        media.caption.strip()
        if media.caption
        else (ctx.message.text or "").strip() or None
    )

    # 检查是否开启翻译模式
    settings = await get_user_settings(user_id)
    translate_mode = settings.get("auto_translate", 0)

    # 发送处理中提示
    if translate_mode:
        thinking_msg = await ctx.reply("🌍 正在翻译语音内容...")
    else:
        thinking_msg = await ctx.reply("🎤 正在识别语音内容...")

    # 发送"正在输入"状态
    await ctx.send_chat_action(action="typing")

    try:
        logger.info("Voice payload loaded: mime=%s duration=%s", mime_type, duration)
        voice_bytes = media.content or b""
        if not voice_bytes:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, "❌ 未能读取语音数据，请重试。")
            return

        # 翻译模式：双语对照输出
        if translate_mode:
            result = await transcribe_and_translate_voice(voice_bytes, mime_type)

            if not result:
                msg_id = getattr(
                    thinking_msg, "message_id", getattr(thinking_msg, "id", None)
                )
                await ctx.edit_message(msg_id, "❌ 无法识别或翻译语音内容，请重试。")
                return

            original_lang = result.get("original_lang", "未知")
            original = result.get("original", "")
            translated = result.get("translated", "")

            output = (
                f"🎤 **语音翻译**\n\n"
                f"📝 **原文** ({original_lang}):\n"
                f"「{original}」\n\n"
                f"🌐 **译文**:\n"
                f"「{translated}」"
            )

            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(msg_id, output)

            # 记录统计
            from stats import increment_stat

            await increment_stat(user_id, "translations_count")
            return

        # 正常模式：转写语音
        transcribed_text = await transcribe_voice(voice_bytes, mime_type)

        if not transcribed_text:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(
                msg_id, "❌ 无法识别语音内容，请重试或发送文字消息。"
            )
            return

        logger.info(f"Voice transcribed: {transcribed_text[:50]}...")

        # 如果用户附带了文字说明（Caption），将其作为指令追加到内容前
        final_text = transcribed_text
        if user_instruction:
            final_text = f"{user_instruction}\n\n【语音内容】：\n{transcribed_text}"
            # 有指令时，视为短语音逻辑处理（走智能路由）
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(
                msg_id, f'🎤 已识别语音内容，正在执行指令: **"{user_instruction}"**...'
            )
            await process_as_text_message(ctx, final_text, thinking_msg)
            return

        # 根据语音时长决定处理策略（若无 duration 属性则默认为长语音）
        # duration variable is already set above
        if duration <= SHORT_VOICE_THRESHOLD:
            # 短语音：走智能路由（与文本消息一致）
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(
                msg_id,
                f'🎤 语音转写内容为: **"{transcribed_text}"**\n\n🤔 正在思考中...',
            )

            # 调用文本消息处理逻辑
            await process_as_text_message(ctx, transcribed_text, thinking_msg)
        else:
            # 长语音：直接输出转写结果
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(
                msg_id, f"🎤 **语音转写结果：**\n\n{transcribed_text}"
            )

            # 记录到上下文
            await add_message(
                ctx, user_id, "user", f"【用户发送了一段长语音】{transcribed_text}"
            )

            # 记录统计
            from stats import increment_stat

            await increment_stat(user_id, "voice_chats")

    except BadRequest as e:
        msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))
        if "File is too big" in str(e):
            await ctx.edit_message(
                msg_id,
                "⚠️ **音频文件过大**\n\n"
                "抱歉，Telegram 限制 Bot 只能下载 **20MB** 以内的文件，我无法获取这段音频。\n\n"
                "💡 **建议方案**：\n"
                "1. 使用音频压缩软件减小体积后重发\n"
                "2. 这是一个 Telegram 官方限制，无法在服务端切割（因为根本下载不到）",
            )
        else:
            logger.error(f"Voice processing BadRequest: {e}")
            await ctx.edit_message(msg_id, "❌ 处理失败：文件格式或内容受限。")

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        try:
            msg_id = getattr(
                thinking_msg, "message_id", getattr(thinking_msg, "id", None)
            )
            await ctx.edit_message(
                msg_id,
                "❌ 语音处理失败，请稍后再试。\n\n"
                "可能的原因：\n"
                "• 语音格式不支持\n"
                "• 语音内容无法识别\n"
                "• 服务暂时不可用",
            )
        except BadRequest:
            pass


async def process_as_text_message(ctx: UnifiedContext, text: str, thinking_msg) -> None:
    """
    将转写后的文本按普通文本消息逻辑处理（代理给 Agent Orchestrator）
    """
    import time
    from core.agent_orchestrator import agent_orchestrator
    from stats import increment_stat

    # Legacy fallbacks
    update = ctx.platform_event

    user_id = ctx.message.user.id

    # 记录用户消息到上下文
    await add_message(ctx, user_id, "user", text)

    # 构建上下文
    context_messages = await get_user_context(ctx, user_id)
    context_messages.append({"role": "user", "parts": [{"text": text}]})

    msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))

    # 代理给 Agent Orchestrator
    try:
        final_text_response = ""
        last_update_time = 0

        async for chunk_text in agent_orchestrator.handle_message(
            ctx, context_messages
        ):
            final_text_response += chunk_text

            now = time.time()
            if now - last_update_time > 0.8:
                await ctx.edit_message(msg_id, final_text_response)
                last_update_time = now

        # 发送最终回复
        if final_text_response:
            await ctx.edit_message(msg_id, final_text_response)
            await add_message(ctx, user_id, "model", final_text_response)
            await increment_stat(user_id, "voice_chats")
        else:
            await ctx.edit_message(msg_id, "抱歉，我无法生成回复。")

    except Exception as e:
        logger.error(f"Voice Agent error: {e}")
        await ctx.edit_message(msg_id, f"❌ Agent 运行出错：{e}")
