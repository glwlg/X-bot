"""
语音消息处理模块

统一将语音转写为文字后，再按普通文本消息处理。
"""

import logging
import base64
import asyncio
import json
import mimetypes
import os
import re
from typing import Any, cast

import httpx
from telegram.error import BadRequest

from core.config import is_user_allowed, get_client_for_model
from core.model_config import select_model_for_role
from core.platform.exceptions import MediaProcessingError
from services.openai_adapter import (
    build_messages,
    create_chat_completion,
    extract_text_from_chat_completion,
)
from user_context import add_message, bind_delivery_target, get_user_context
from core.platform.models import MessageType, UnifiedContext
from .ai_handlers import _acknowledge_received
from .base_handlers import require_feature_access
from .media_utils import extract_media_input

logger = logging.getLogger(__name__)

# Backward-compatible async client injection for tests/legacy callers.
openai_async_client: Any = None


def _whisper_http_endpoint() -> str:
    return str(
        os.getenv("VIDEO_TO_TEXT_WHISPER_ENDPOINT")
        or os.getenv("WHISPER_INFERENCE_URL")
        or ""
    ).strip()


def _whisper_http_enabled() -> bool:
    return bool(_whisper_http_endpoint())


def _whisper_http_timeout_seconds() -> float:
    try:
        value = float(os.getenv("VIDEO_TO_TEXT_WHISPER_TIMEOUT_SECONDS", "180"))
    except Exception:
        value = 180.0
    return max(5.0, value)


def _whisper_http_response_format() -> str:
    return str(os.getenv("VIDEO_TO_TEXT_WHISPER_RESPONSE_FORMAT") or "text").strip() or "text"


def _whisper_http_language() -> str:
    return str(os.getenv("VIDEO_TO_TEXT_WHISPER_LANGUAGE") or "zh").strip()


def _whisper_http_temperature() -> float:
    try:
        value = float(os.getenv("VIDEO_TO_TEXT_WHISPER_TEMPERATURE", "0"))
    except Exception:
        value = 0.0
    return max(0.0, value)


def _whisper_http_temperature_inc() -> float:
    try:
        value = float(os.getenv("VIDEO_TO_TEXT_WHISPER_TEMPERATURE_INC", "0"))
    except Exception:
        value = 0.0
    return max(0.0, value)


def _whisper_http_no_timestamps() -> bool:
    raw = str(os.getenv("VIDEO_TO_TEXT_WHISPER_NO_TIMESTAMPS", "1")).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _extract_weixin_voice_transcript(ctx: UnifiedContext) -> str:
    raw_data = getattr(ctx.message, "raw_data", None)
    if not isinstance(raw_data, dict):
        return ""
    for item in raw_data.get("item_list") or []:
        if not isinstance(item, dict) or item.get("type") != 3:
            continue
        voice_item = item.get("voice_item") or {}
        transcript = _normalize_transcribed_text(voice_item.get("text") or "")
        if transcript:
            return transcript
    return ""


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
    return extract_text_from_chat_completion(response)


def _parse_audio_response_payload(raw_text: str) -> tuple[str, str]:
    text = str(raw_text or "").strip()
    if not text:
        return "empty", ""

    candidates = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.I))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        error_text = _normalize_transcribed_text(str(payload.get("error") or ""))
        if error_text:
            return "failed", error_text
        status = str(payload.get("status") or "").strip().lower() or "transcribed"
        transcript = _normalize_transcribed_text(
            str(
                payload.get("transcript")
                or payload.get("text")
                or payload.get("content")
                or ""
            )
        )
        return status, transcript

    return "unstructured", _normalize_transcribed_text(text)


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


def _audio_suffix_for_mime(mime_type: str) -> str:
    base = _audio_base_mime(mime_type)
    mapping = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/webm": ".webm",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/flac": ".flac",
    }
    guessed = mimetypes.guess_extension(base or "")
    return mapping.get(base, guessed or ".bin")


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
    voice_model = select_model_for_role("voice")
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
            response = await create_chat_completion(
                async_client=cast(Any, client),
                model=voice_model,
                messages=build_messages(
                    contents=_build_audio_contents(
                        prompt, candidate_bytes, candidate_mime
                    ),
                ),
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            try:
                response = await create_chat_completion(
                    async_client=cast(Any, client),
                    model=voice_model,
                    messages=build_messages(
                        contents=_build_audio_contents(
                            prompt, candidate_bytes, candidate_mime
                        ),
                    ),
                )
            except Exception as inner_exc:
                last_error = inner_exc
                logger.warning(
                    "Voice model call failed with model=%s mime=%s source=%s err=%s",
                    voice_model,
                    candidate_mime,
                    source,
                    inner_exc,
                )
                continue

        text = _extract_model_text(response)
        status, transcript = _parse_audio_response_payload(text)
        if status in {"no_audio", "unintelligible", "empty"}:
            logger.info(
                "Voice model returned non-transcribable status=%s model=%s mime=%s",
                status,
                voice_model,
                candidate_mime,
            )
            continue

        if text and not transcript:
            logger.info("Voice model returned only quotes, retrying...")
            continue

        if transcript:
            return transcript

    if last_error is not None:
        logger.error("Voice model call failed after mime retries: %s", last_error)
    else:
        logger.warning(
            "Voice model returned empty transcript after %s attempts", len(attempts)
        )
    return ""


async def _transcribe_voice_with_whisper_http(
    voice_bytes: bytes,
    mime_type: str,
) -> str:
    endpoint = _whisper_http_endpoint()
    if not endpoint:
        return ""

    payload_bytes = bytes(voice_bytes)
    safe_mime_type = _audio_base_mime(mime_type) or "application/octet-stream"
    if safe_mime_type in {
        "audio/ogg",
        "application/ogg",
        "audio/opus",
        "audio/x-opus",
        "audio/webm",
    }:
        transcoded_wav = await _transcode_audio_to_wav(voice_bytes, mime_type)
        if transcoded_wav:
            payload_bytes = transcoded_wav
            safe_mime_type = "audio/wav"
            logger.info(
                "Voice transcription transcoded audio for Whisper HTTP upload: original_mime=%s upload_mime=%s upload_size=%s",
                mime_type,
                safe_mime_type,
                len(payload_bytes),
            )

    suffix = _audio_suffix_for_mime(safe_mime_type)
    data = {
        "response_format": _whisper_http_response_format(),
        "temperature": f"{_whisper_http_temperature():.2f}",
        "temperature_inc": f"{_whisper_http_temperature_inc():.2f}",
    }
    language = _whisper_http_language()
    if language:
        data["language"] = language
    if _whisper_http_no_timestamps():
        data["no_timestamps"] = "true"

    timeout_seconds = _whisper_http_timeout_seconds()
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    files = {"file": (f"audio{suffix}", payload_bytes, safe_mime_type)}

    try:
        logger.info(
            "Voice transcription using Whisper HTTP endpoint=%s mime=%s size=%s",
            endpoint,
            safe_mime_type,
            len(payload_bytes),
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, data=data, files=files)
            response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning(
            "Whisper HTTP request timed out after %.1fs, falling back to voice model",
            timeout_seconds,
        )
        return ""
    except httpx.HTTPError as exc:
        logger.warning("Whisper HTTP request failed, falling back to voice model: %s", exc)
        return ""

    raw_text = str(response.text or "").strip()
    preview = raw_text[:200].replace("\n", "\\n")
    response_headers = getattr(response, "headers", {}) or {}
    logger.info(
        "Whisper HTTP response received: content_type=%s preview=%s",
        response_headers.get("content-type", ""),
        preview,
    )
    if not raw_text:
        logger.warning("Whisper HTTP returned empty transcript body, falling back to voice model")
        return ""

    status, transcript = _parse_audio_response_payload(raw_text)
    if status == "unstructured":
        normalized = _normalize_transcribed_text(raw_text)
        if not normalized:
            logger.warning(
                "Whisper HTTP returned unstructured but unusable transcript preview=%s",
                preview,
            )
        return normalized
    if status == "transcribed":
        if not transcript:
            logger.warning(
                "Whisper HTTP returned transcribed status but empty transcript preview=%s",
                preview,
            )
        return transcript
    if status == "failed":
        logger.warning(
            "Whisper HTTP returned error payload detail=%s preview=%s",
            transcript,
            preview,
        )
        return ""
    logger.warning(
        "Whisper HTTP returned non-transcribable status=%s preview=%s",
        status,
        preview,
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
        if _whisper_http_enabled():
            whisper_text = _normalize_transcribed_text(
                await _transcribe_voice_with_whisper_http(voice_bytes, mime_type)
            )
            if whisper_text:
                logger.info("Voice transcription completed via Whisper HTTP")
                return whisper_text
            logger.warning("Whisper HTTP did not yield usable text, falling back to voice model")
        prompt = (
            "请将这段语音转写为文字。"
            "返回 JSON，格式为 "
            '{"status":"transcribed|no_audio|unintelligible","transcript":"..."}。'
            "如果成功识别，status=transcribed，transcript 只保留语音原话，不要添加解释。"
            "如果没有收到可用音频或无法识别，transcript 置空。"
        )
        text = _normalize_transcribed_text(await _run_audio_prompt(prompt, voice_bytes, mime_type))
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
    if not await require_feature_access(ctx, "chat"):
        return

    await _acknowledge_received(ctx)

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

        await bind_delivery_target(ctx, user_id)
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
        transcribed_text = _extract_weixin_voice_transcript(ctx)
        if transcribed_text:
            logger.info(
                "Using embedded Weixin voice transcript fallback for user_id=%s",
                ctx.message.user.id,
            )
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
            await ctx.edit_message(
                msg_id,
                final_text_response,
                run_after_reply_hooks=True,
            )
            await add_message(ctx, user_id, "model", final_text_response)
            await increment_stat(user_id, "voice_chats")
        else:
            await ctx.edit_message(msg_id, "抱歉，我无法生成回复。")

    except Exception as e:
        logger.error(f"Voice Agent error: {e}")
        await ctx.edit_message(msg_id, f"❌ Agent 运行出错：{e}")
