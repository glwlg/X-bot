from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

from core.extension_base import PluginExtension
from core.reply_hooks import text_reply_hook_registry
from core.runtime_config_store import runtime_config_store
from services.tts_service import (
    synthesize_edge_tts_speech,
    transcode_audio_bytes_to_ogg_opus,
)

logger = logging.getLogger(__name__)

_SUPPORTED_PLATFORMS = {"telegram", "discord", "web"}
_TTS_TERM_REPLACEMENTS = (
    (re.compile(r"\bikaros\b", flags=re.I), "伊卡洛斯"),
)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002600-\U000027BF"
    "\u200d"
    "\uFE0F"
    "]+",
    flags=re.UNICODE,
)
_RAW_SKIP_PREFIXES = (
    "🤔",
    "📄 正在",
    "🎤 正在",
    "⏳",
    "⚠",
    "❌",
    "🔄",
    "🔍",
    "🛑",
    "🔇",
)
_VOICE_OUTPUT_DEDUPE_TTL_SECONDS = 120.0
_VOICE_OUTPUT_DEDUPE_MAX_KEYS = 512
_voice_output_recent_keys: dict[str, float] = {}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _command_args(ctx) -> list[str]:
    message_text = _safe_text(getattr(getattr(ctx, "message", None), "content", ""))
    if message_text.startswith("/"):
        parts = message_text.split(maxsplit=1)
        if len(parts) >= 2:
            return [item for item in parts[1].split() if item]
    raw_args = getattr(getattr(ctx, "platform_ctx", None), "args", None)
    if isinstance(raw_args, list):
        return [_safe_text(item) for item in raw_args if _safe_text(item)]
    return []


def _voice_output_summary() -> str:
    config = runtime_config_store.get_voice_output_config()
    enabled = bool(config.get("enabled"))
    platforms = [
        _safe_text(item).lower()
        for item in list(config.get("platforms") or [])
        if _safe_text(item)
    ]
    lines = [
        "🔊 Ikaros 语音输出",
        "",
        f"状态：{'开启' if enabled else '关闭'}",
        f"引擎：{_safe_text(config.get('provider')) or 'edge_tts'}",
        f"音色：{_safe_text(config.get('voice')) or 'zh-CN-XiaoxiaoNeural'}",
        f"平台：{', '.join(platforms) if platforms else 'telegram, discord, web'}",
        "",
        "用法：`/voiceout on`、`/voiceout off`、`/voiceout status`",
    ]
    return "\n".join(lines)


def _is_control_interaction(ctx) -> bool:
    inbound_text = _safe_text(getattr(getattr(ctx, "message", None), "content", ""))
    if inbound_text.startswith("/"):
        return True
    callback_data = _safe_text(getattr(ctx, "callback_data", ""))
    if callback_data:
        return True
    return False


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub(" ", str(text or ""))


def _normalize_tts_terms(text: str) -> str:
    rendered = str(text or "")
    for pattern, replacement in _TTS_TERM_REPLACEMENTS:
        rendered = pattern.sub(replacement, rendered)
    return rendered


def _plain_text_for_tts(text: str) -> str:
    rendered = _safe_text(text)
    if not rendered:
        return ""
    rendered = re.sub(r"```[\s\S]*?```", " ", rendered)
    rendered = re.sub(r"`([^`]+)`", r"\1", rendered)
    rendered = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", rendered)
    rendered = re.sub(r"^>\s*", "", rendered, flags=re.M)
    rendered = re.sub(r"[*_#~-]+", " ", rendered)
    rendered = _strip_emoji(rendered)
    rendered = _normalize_tts_terms(rendered)
    rendered = re.sub(r"\s+", " ", rendered)
    return rendered.strip()


def _voice_output_dedupe_key(ctx, rendered_text: str) -> str:
    message = getattr(ctx, "message", None)
    chat = getattr(message, "chat", None)
    user = getattr(message, "user", None)
    message_date = getattr(message, "date", None)
    if hasattr(message_date, "isoformat"):
        date_key = message_date.isoformat()
    else:
        date_key = _safe_text(message_date)

    text_digest = hashlib.sha256(str(rendered_text or "").encode("utf-8")).hexdigest()
    return "|".join(
        (
            _safe_text(getattr(message, "platform", "")).lower(),
            _safe_text(getattr(chat, "id", "")),
            _safe_text(getattr(user, "id", ""))
            or _safe_text(getattr(ctx, "effective_user_id", "")),
            _safe_text(getattr(message, "id", "")),
            date_key,
            text_digest[:24],
        )
    )


def _claim_voice_output(ctx, rendered_text: str) -> bool:
    now = time.monotonic()
    expired = [
        key
        for key, seen_at in _voice_output_recent_keys.items()
        if now - seen_at > _VOICE_OUTPUT_DEDUPE_TTL_SECONDS
    ]
    for key in expired:
        _voice_output_recent_keys.pop(key, None)

    key = _voice_output_dedupe_key(ctx, rendered_text)
    if key in _voice_output_recent_keys:
        logger.info("Skip duplicate voice output for key=%s", key)
        return False

    _voice_output_recent_keys[key] = now
    if len(_voice_output_recent_keys) > _VOICE_OUTPUT_DEDUPE_MAX_KEYS:
        oldest = sorted(
            _voice_output_recent_keys.items(),
            key=lambda item: item[1],
        )
        for old_key, _ in oldest[: len(_voice_output_recent_keys) // 4]:
            _voice_output_recent_keys.pop(old_key, None)
    return True


def _should_emit_voice_output(ctx, text: str) -> bool:
    if not runtime_config_store.is_voice_output_enabled(default=False):
        return False
    if _is_control_interaction(ctx):
        return False
    raw_text = _safe_text(text)
    if raw_text.startswith(_RAW_SKIP_PREFIXES):
        return False
    platform = _safe_text(getattr(getattr(ctx, "message", None), "platform", "")).lower()
    if platform not in _SUPPORTED_PLATFORMS:
        return False

    config = runtime_config_store.get_voice_output_config()
    allowed_platforms = {
        _safe_text(item).lower()
        for item in list(config.get("platforms") or [])
        if _safe_text(item)
    }
    if allowed_platforms and platform not in allowed_platforms:
        return False

    rendered = _plain_text_for_tts(text)
    if not rendered:
        return False
    if "http://" in rendered or "https://" in rendered:
        return False

    try:
        min_chars = int(config.get("min_chars") or 12)
    except Exception:
        min_chars = 12
    try:
        max_chars = int(config.get("max_chars") or 1200)
    except Exception:
        max_chars = 1200
    if len(rendered) < max(1, min_chars):
        return False
    if max_chars > 0 and len(rendered) > max_chars:
        return False
    return True


async def _deliver_voice_output(ctx, audio_bytes: bytes) -> None:
    adapter = getattr(ctx, "_adapter", None)
    platform = _safe_text(getattr(getattr(ctx, "message", None), "platform", "")).lower()
    if platform == "telegram":
        reply_voice = getattr(adapter, "reply_voice", None)
        if callable(reply_voice):
            voice_bytes = await transcode_audio_bytes_to_ogg_opus(audio_bytes)
            if voice_bytes:
                try:
                    await reply_voice(ctx, voice_bytes, filename="voice.ogg")
                    return
                except Exception:
                    logger.warning(
                        "Telegram voice-note delivery failed after send attempt; "
                        "skip audio fallback to avoid duplicate delivery.",
                        exc_info=True,
                    )
                    return
            else:
                logger.info("Telegram voice transcode unavailable; fallback to audio.")
    await ctx.reply_audio(audio_bytes, filename="voice.mp3")


async def _after_text_reply(ctx, text: str, response: Any) -> None:
    _ = response
    if not _should_emit_voice_output(ctx, text):
        return

    config = runtime_config_store.get_voice_output_config()
    rendered = _plain_text_for_tts(text)
    if not _claim_voice_output(ctx, rendered):
        return

    audio_bytes = await synthesize_edge_tts_speech(
        rendered,
        voice=_safe_text(config.get("voice")) or "zh-CN-XiaoxiaoNeural",
        rate=_safe_text(config.get("rate")) or "+0%",
        volume=_safe_text(config.get("volume")) or "+0%",
        pitch=_safe_text(config.get("pitch")) or "+0Hz",
    )
    if not audio_bytes:
        return

    try:
        await _deliver_voice_output(ctx, audio_bytes)
    except Exception:
        logger.warning("Voice output delivery failed.", exc_info=True)


async def voiceout_command(ctx) -> None:
    args = _command_args(ctx)
    action = _safe_text(args[0]).lower() if args else "status"
    actor = (
        f"{_safe_text(getattr(getattr(ctx, 'message', None), 'platform', ''))}:"
        f"{ctx.effective_user_id or 'unknown'}"
    )

    if action in {"on", "enable", "start"}:
        runtime_config_store.set_voice_output_enabled(
            True,
            actor=actor,
            reason="voiceout_command_enable",
        )
        await ctx.reply("🔊 已开启 Ikaros 语音输出。后续文本回复会追加语音。")
        return

    if action in {"off", "disable", "stop"}:
        runtime_config_store.set_voice_output_enabled(
            False,
            actor=actor,
            reason="voiceout_command_disable",
        )
        await ctx.reply("🔇 已关闭 Ikaros 语音输出。")
        return

    if action in {"status", ""}:
        await ctx.reply(_voice_output_summary())
        return

    await ctx.reply(
        "用法：`/voiceout on`、`/voiceout off`、`/voiceout status`"
    )


class EdgeTtsPlugin(PluginExtension):
    name = "edge_tts"
    priority = 30

    def register(self, runtime) -> None:
        _ = runtime
        runtime.register_command(
            "voiceout",
            voiceout_command,
            description="开关 Ikaros 语音输出",
        )
        text_reply_hook_registry.register_after_reply(
            _after_text_reply,
            owner=self.name,
            priority=self.priority,
        )
