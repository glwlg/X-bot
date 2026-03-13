"""
语音消息处理模块

统一将语音转写为文字后，再按普通文本消息处理。
"""

import logging
import base64
import asyncio
import json
import re
from typing import Any, cast
from telegram.error import BadRequest

from core.config import is_user_allowed, get_client_for_model
from core.model_config import get_voice_model
from core.platform.exceptions import MediaProcessingError
from services.openai_adapter import build_messages
from user_context import add_message, get_user_context
from core.platform.models import MessageType, UnifiedContext
from .media_utils import extract_media_input

logger = logging.getLogger(__name__)

# Backward-compatible async client injection for tests/legacy callers.
openai_async_client: Any = None


def _resolve_voice_client(model_name: str) -> Any:
    if openai_async_client is not None:
        return openai_async_client
    return get_client_for_model(model_name, is_async=True)


def _normalize_transcribed_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""

    for wrapped in ("```json", "```"):
        if text.lower().startswith(wrapped):
            text = text[len(wrapped) :].strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    if text.lower().startswith("json"):
        text = text[4:].strip()

    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for key in ("text", "transcript", "content", "result"):
                    value = parsed.get(key)
                    if isinstance(value, str) and value.strip():
                        text = value.strip()
                        break
        except Exception:
            pass

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

    choices = getattr(response, "choices", None) or []
    for choice in choices:
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    chunks.append(str(part.get("text") or ""))
            merged = "\n".join([item for item in chunks if item]).strip()
            if merged:
                return merged

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


def _audio_base_mime(mime_type: str) -> str:
    return str(mime_type or "").split(";", 1)[0].strip().lower()


def _sniff_audio_container(voice_bytes: bytes) -> str | None:
    if not voice_bytes:
        return None

    head16 = bytes(voice_bytes[:16])
    head32 = bytes(voice_bytes[:32])

    if head16.startswith(b"OggS"):
        return "ogg"
    if len(head16) >= 12 and head16.startswith(b"RIFF") and head16[8:12] == b"WAVE":
        return "wav"
    if head16.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if head16.startswith(b"fLaC"):
        return "flac"
    if head16.startswith(b"ID3"):
        return "mp3"
    if len(head16) >= 2 and head16[0] == 0xFF and (head16[1] & 0xE0) == 0xE0:
        return "mp3"
    if b"ftyp" in head32:
        return "mp4"
    return None


def _ffmpeg_input_format(mime_type: str, voice_bytes: bytes) -> str | None:
    base = _audio_base_mime(mime_type)
    if base in {"audio/ogg", "application/ogg", "audio/opus", "audio/x-opus"}:
        return "ogg"
    if base in {"audio/webm"}:
        return "webm"
    if base in {"audio/mp4", "audio/x-m4a"}:
        return "mp4"
    if base in {"audio/aac", "audio/x-aac"}:
        return "aac"
    if "mpeg" in base or "mp3" in base:
        return "mp3"
    if "wav" in base:
        return "wav"
    if "flac" in base:
        return "flac"

    sniffed = _sniff_audio_container(voice_bytes)
    if sniffed in {"ogg", "webm", "mp4", "aac", "mp3", "wav", "flac"}:
        return sniffed
    return None


def _should_try_wav_transcode(mime_type: str, voice_bytes: bytes) -> bool:
    base = _audio_base_mime(mime_type)
    if "wav" in base:
        return False

    if base in {
        "audio/ogg",
        "application/ogg",
        "audio/opus",
        "audio/x-opus",
        "audio/webm",
        "audio/mp4",
        "audio/x-m4a",
        "audio/aac",
        "audio/x-aac",
        "audio/flac",
    }:
        return True

    return _sniff_audio_container(voice_bytes) in {"ogg", "webm", "mp4", "flac"}


async def _transcode_audio_to_wav(voice_bytes: bytes, mime_type: str) -> bytes | None:
    if not voice_bytes or not _should_try_wav_transcode(mime_type, voice_bytes):
        return None

    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    input_fmt = _ffmpeg_input_format(mime_type, voice_bytes)
    if input_fmt:
        cmd.extend(["-f", input_fmt])
    cmd.extend(["-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"])

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(input=bytes(voice_bytes))
        if process.returncode != 0:
            logger.warning(
                "Voice transcode failed: mime=%s code=%s err=%s",
                mime_type,
                process.returncode,
                (stderr or b"")[:200].decode("utf-8", errors="ignore"),
            )
            return None
        if stdout:
            return stdout
    except FileNotFoundError:
        logger.warning("Voice transcode skipped: ffmpeg not found")
    except Exception as exc:
        logger.warning("Voice transcode failed: mime=%s err=%s", mime_type, exc)
    return None


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
    voice_model = get_voice_model()
    client: Any = _resolve_voice_client(voice_model)
    if client is None:
        logger.error("Voice model call skipped: OpenAI async client is not initialized")
        return ""

    attempts: list[tuple[str, bytes, str]] = []
    transcoded_wav = await _transcode_audio_to_wav(voice_bytes, mime_type)
    if transcoded_wav:
        attempts.append(("audio/wav", transcoded_wav, "transcoded_wav"))
    for candidate_mime in _audio_mime_candidates(mime_type):
        attempts.append((candidate_mime, voice_bytes, "raw"))

    for candidate_mime, candidate_bytes, source in attempts:
        try:
            response = await cast(Any, client).chat.completions.create(
                model=voice_model,
                messages=build_messages(
                    contents=_build_audio_contents(
                        prompt, candidate_bytes, candidate_mime
                    ),
                ),
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Voice model call failed with model=%s mime=%s source=%s err=%s",
                voice_model,
                candidate_mime,
                source,
                exc,
            )
            continue

        text = _extract_model_text(response)
        if text and _looks_like_audio_missing_reply(text):
            logger.info(
                "Voice model could not consume audio: model=%s mime=%s",
                voice_model,
                candidate_mime,
            )
            continue

        normalized = _normalize_transcribed_text(text)
        if text and not normalized:
            logger.info("Voice model returned only quotes, retrying...")
            continue

        if text:
            return text

    if last_error is not None:
        logger.error("Voice model call failed after mime retries: %s", last_error)
    else:
        logger.warning(
            "Voice model returned empty transcript after %s attempts", len(attempts)
        )
    return ""


async def transcribe_voice(voice_bytes: bytes, mime_type: str) -> str | None:
    """
    使用对话模型转写语音为文字

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
        return None
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        return None


async def handle_voice_message(ctx: UnifiedContext) -> None:
    user_id = ctx.message.user.id

    # 检查用户权限
    if not await is_user_allowed(user_id):
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
    duration = int(media.meta.get("duration") or 0)
    user_instruction = (
        media.caption.strip()
        if media.caption
        else (ctx.message.text or "").strip() or None
    )

    thinking_msg = await ctx.reply("🎤 正在理解语音内容...")

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

        await process_as_voice_message(
            ctx=ctx,
            voice_bytes=voice_bytes,
            mime_type=mime_type,
            user_instruction=user_instruction,
            thinking_msg=thinking_msg,
        )

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


def _looks_like_audio_missing_reply(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if ("没有收到" in lowered or "未收到" in lowered) and (
        "语音" in lowered or "音频" in lowered
    ):
        return True
    cues = (
        "没有附上语音",
        "没有附上音频",
        "语音内容/音频文件",
        "音频文件",
        "无法听到",
        "请上传语音",
        "语音文件/语音链接",
        "no audio",
        "no voice",
        "voice file",
        "audio file",
        "upload audio",
        "attach audio",
    )
    return any(cue in lowered for cue in cues)


async def process_as_voice_message(
    ctx: UnifiedContext,
    voice_bytes: bytes,
    mime_type: str,
    user_instruction: str | None,
    thinking_msg: Any,
) -> None:
    """
    语音消息处理：先转写为文字，再作为文本消息走 Agent 处理。

    不再尝试多模态直接理解音频（不稳定，常返回"没收到音频"导致体验差）。
    """
    msg_id = getattr(thinking_msg, "message_id", getattr(thinking_msg, "id", None))

    # ── Step 1: 转写语音 ──
    transcribed_text = await transcribe_voice(voice_bytes, mime_type)
    if not transcribed_text:
        await ctx.edit_message(msg_id, "❌ 无法识别语音内容，请重试或改用文字发送。")
        return

    # ── Step 2: 组装文本 ──
    instruction = str(user_instruction or "").strip()
    if instruction:
        final_text = f"{instruction}\n\n【语音内容】：\n{transcribed_text}"
    else:
        final_text = transcribed_text

    # ── Step 3: 走文本消息处理 ──
    await ctx.edit_message(
        msg_id,
        f"🎤 语音已识别，正在处理...\n\n> {transcribed_text[:100]}{'...' if len(transcribed_text) > 100 else ''}",
    )
    await process_as_text_message(ctx, final_text, thinking_msg)


async def process_as_text_message(ctx: UnifiedContext, text: str, thinking_msg) -> None:
    """
    将转写后的文本按普通文本消息逻辑处理（代理给 Agent Orchestrator）
    """
    import time
    from core.agent_orchestrator import agent_orchestrator
    from stats import increment_stat

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
