from __future__ import annotations

from typing import Any

from core.config import get_client_for_model
from core.model_config import get_model_id_for_api, get_voice_model


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
    model_key = str(get_voice_model() or "").strip()
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
