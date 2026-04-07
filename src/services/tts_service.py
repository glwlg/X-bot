from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any

from core.config import get_client_for_model
from core.model_config import get_model_id_for_api, select_model_for_role

logger = logging.getLogger(__name__)


def _response_bytes(response: Any) -> bytes:
    if response is None:
        return b""
    for attr in ("content", "data"):
        value = getattr(response, attr, None)
        if isinstance(value, bytes):
            return value
    read = getattr(response, "read", None)
    if callable(read):
        result = read()
        if isinstance(result, bytes):
            return result
    aread = getattr(response, "aread", None)
    if callable(aread):
        return b""
    return b""


async def synthesize_speech(
    text: str,
    *,
    voice: str = "alloy",
    response_format: str = "mp3",
    instructions: str = "",
) -> bytes:
    model_key = str(select_model_for_role("voice") or "").strip()
    if not model_key:
        return b""
    client = get_client_for_model(model_key, is_async=True)
    if client is None:
        return b""
    model_id = get_model_id_for_api(model_key)
    if not model_id:
        return b""

    kwargs = {
        "model": model_id,
        "voice": str(voice or "alloy").strip() or "alloy",
        "input": str(text or "").strip(),
        "response_format": str(response_format or "mp3").strip() or "mp3",
    }
    if str(instructions or "").strip():
        kwargs["instructions"] = str(instructions).strip()
    if not kwargs["input"]:
        return b""

    response = await client.audio.speech.create(**kwargs)
    payload = _response_bytes(response)
    if payload:
        return payload

    aread = getattr(response, "aread", None)
    if callable(aread):
        data = await aread()
        if isinstance(data, bytes):
            return data
    return b""


async def synthesize_edge_tts_speech(
    text: str,
    *,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> bytes:
    payload = str(text or "").strip()
    if not payload:
        return b""

    try:
        import edge_tts
    except Exception:
        logger.warning("edge-tts is unavailable; skip voice output.", exc_info=True)
        return b""

    communicate = edge_tts.Communicate(
        payload,
        voice=str(voice or "zh-CN-XiaoxiaoNeural").strip() or "zh-CN-XiaoxiaoNeural",
        rate=str(rate or "+0%").strip() or "+0%",
        volume=str(volume or "+0%").strip() or "+0%",
        pitch=str(pitch or "+0Hz").strip() or "+0Hz",
    )
    chunks = bytearray()
    async for item in communicate.stream():
        if item.get("type") != "audio":
            continue
        data = item.get("data")
        if isinstance(data, bytes):
            chunks.extend(data)
        elif isinstance(data, bytearray):
            chunks.extend(bytes(data))
    return bytes(chunks)


async def transcode_audio_bytes_to_ogg_opus(
    audio_bytes: bytes,
    *,
    input_suffix: str = ".mp3",
    bitrate: str = "32k",
) -> bytes:
    payload = bytes(audio_bytes or b"")
    if not payload:
        return b""

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        logger.warning("ffmpeg is unavailable; skip Telegram voice transcoding.")
        return b""

    source_path = ""
    target_path = ""
    try:
        source_fd, source_path = tempfile.mkstemp(suffix=input_suffix)
        target_fd, target_path = tempfile.mkstemp(suffix=".ogg")
        os.close(source_fd)
        os.close(target_fd)
        with open(source_path, "wb") as handle:
            handle.write(payload)

        process = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            "-y",
            "-i",
            source_path,
            "-vn",
            "-ac",
            "1",
            "-c:a",
            "libopus",
            "-b:a",
            str(bitrate or "32k"),
            target_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(
                "ffmpeg voice transcode failed: %s",
                stderr.decode("utf-8", errors="ignore").strip(),
            )
            return b""
        with open(target_path, "rb") as handle:
            return handle.read()
    except Exception:
        logger.warning("ffmpeg voice transcode crashed.", exc_info=True)
        return b""
    finally:
        for path in (source_path, target_path):
            if path:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except Exception:
                    logger.debug("Failed to remove temp audio file: %s", path, exc_info=True)
