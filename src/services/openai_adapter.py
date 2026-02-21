from __future__ import annotations

import json
import os
from typing import Any


_AUDIO_PART_STYLE = str(os.getenv("OPENAI_AUDIO_PART_STYLE") or "file").strip().lower()


def build_messages(
    *,
    contents: Any,
    system_instruction: str | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system_text = str(system_instruction or "").strip()
    if system_text:
        messages.append({"role": "system", "content": system_text})

    if isinstance(contents, str):
        text = contents.strip()
        if text:
            messages.append({"role": "user", "content": text})
        return messages

    for item in _iter_content_items(contents):
        role = _normalize_role(_read_attr(item, "role") or _read_key(item, "role"))
        parts = _read_attr(item, "parts") or _read_key(item, "parts")
        if not parts and isinstance(item, dict) and item.get("content"):
            parts = [{"text": str(item.get("content"))}]

        content_blocks = _build_content_blocks(parts)
        if not content_blocks:
            continue

        if len(content_blocks) == 1 and content_blocks[0].get("type") == "text":
            messages.append(
                {
                    "role": role,
                    "content": str(content_blocks[0].get("text") or ""),
                }
            )
            continue

        messages.append({"role": role, "content": content_blocks})

    return messages


def build_tools(raw_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in raw_tools or []:
        declarations = _read_attr(tool, "function_declarations")
        if declarations is None and isinstance(tool, dict):
            declarations = tool.get("function_declarations")
        for declaration in declarations or []:
            if not isinstance(declaration, dict):
                continue
            name = str(declaration.get("name") or "").strip()
            if not name:
                continue
            parameters = declaration.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}}
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(declaration.get("description") or ""),
                        "parameters": parameters,
                    },
                }
            )
    return tools


def apply_generation_config(
    *,
    kwargs: dict[str, Any],
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(config, dict):
        return kwargs

    if "temperature" in config:
        kwargs["temperature"] = config["temperature"]
    if "top_p" in config:
        kwargs["top_p"] = config["top_p"]
    if "max_output_tokens" in config:
        kwargs["max_tokens"] = config["max_output_tokens"]
    if config.get("response_mime_type") == "application/json":
        kwargs["response_format"] = {"type": "json_object"}

    tools = build_tools(config.get("tools"))
    if tools:
        kwargs["tools"] = tools

    return kwargs


def build_chat_kwargs(
    *,
    model: str,
    contents: Any,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    kwargs = {
        "model": model,
        "messages": build_messages(
            contents=contents,
            system_instruction=str(cfg.get("system_instruction") or "") or None,
        ),
    }
    return apply_generation_config(kwargs=kwargs, config=cfg)


async def generate_text(
    *,
    async_client: Any,
    model: str,
    contents: Any,
    config: dict[str, Any] | None = None,
) -> str:
    kwargs = build_chat_kwargs(model=model, contents=contents, config=config)
    response = await async_client.chat.completions.create(**kwargs)
    return extract_text_from_chat_completion(response)


def generate_text_sync(
    *,
    sync_client: Any,
    model: str,
    contents: Any,
    config: dict[str, Any] | None = None,
) -> str:
    kwargs = build_chat_kwargs(model=model, contents=contents, config=config)
    response = sync_client.chat.completions.create(**kwargs)
    return extract_text_from_chat_completion(response)


def extract_text_from_chat_completion(response: Any) -> str:
    choices = list(getattr(response, "choices", []) or [])
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_fragments = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_fragments.append(str(item.get("text") or ""))
        return "".join(text_fragments).strip()
    return ""


def extract_tool_calls_from_chat_completion(response: Any) -> list[dict[str, Any]]:
    choices = list(getattr(response, "choices", []) or [])
    if not choices:
        return []
    message = getattr(choices[0], "message", None)
    tool_calls = getattr(message, "tool_calls", None) or []
    output: list[dict[str, Any]] = []
    for call in tool_calls:
        function = getattr(call, "function", None)
        name = str(getattr(function, "name", "") or "").strip()
        if not name:
            continue
        raw_args = getattr(function, "arguments", "") or ""
        args: dict[str, Any] = {}
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    args = parsed
            except Exception:
                args = {}
        output.append(
            {
                "id": str(getattr(call, "id", "") or "").strip(),
                "name": name,
                "args": args,
            }
        )
    return output


def _iter_content_items(contents: Any) -> list[Any]:
    if isinstance(contents, list):
        return contents
    if contents is None:
        return []
    return [contents]


def _normalize_role(raw_role: Any) -> str:
    role = str(raw_role or "user").strip().lower()
    if role == "model":
        return "assistant"
    if role not in {"user", "assistant", "system", "tool"}:
        return "user"
    return role


def _read_key(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return None


def _read_attr(payload: Any, key: str) -> Any:
    return getattr(payload, key, None)


def _build_content_blocks(parts: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in parts or []:
        text = _read_attr(part, "text") or _read_key(part, "text")
        if text:
            blocks.append({"type": "text", "text": str(text)})
            continue

        inline_data = _read_attr(part, "inline_data") or _read_key(part, "inline_data")
        if not isinstance(inline_data, dict):
            continue

        mime_type = str(inline_data.get("mime_type") or "application/octet-stream")
        raw_data = str(inline_data.get("data") or "")
        if not raw_data:
            continue

        if mime_type.startswith("image/"):
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{raw_data}"},
                }
            )
            continue

        if mime_type.startswith("audio/"):
            if _AUDIO_PART_STYLE == "input_audio":
                audio_format = _audio_format_from_mime(mime_type)
                if audio_format:
                    blocks.append(
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": raw_data,
                                "format": audio_format,
                            },
                        }
                    )
                    continue

            blocks.append(
                {
                    "type": "file",
                    "file": {
                        "filename": _audio_filename_from_mime(mime_type),
                        "file_data": raw_data,
                    },
                }
            )
            continue

        blocks.append(
            {
                "type": "text",
                "text": f"[Binary attachment omitted: {mime_type}]",
            }
        )

    return blocks


def _audio_format_from_mime(mime_type: str) -> str | None:
    lowered = str(mime_type or "").strip().lower()
    if not lowered:
        return None

    base = lowered.split(";", 1)[0].strip()

    if "wav" in base or base.endswith("/x-wav"):
        return "wav"
    if "mp3" in base or "mpeg" in base:
        return "mp3"
    if base in {"audio/ogg", "application/ogg"}:
        return "ogg"
    if base in {"audio/opus", "audio/x-opus"}:
        return "opus"
    if base == "audio/webm":
        return "webm"
    if base in {"audio/mp4", "audio/x-m4a"} or "m4a" in base:
        return "mp4"
    if "flac" in base:
        return "flac"
    if base in {"audio/aac", "audio/x-aac"}:
        return "aac"
    return None


def _audio_filename_from_mime(mime_type: str) -> str:
    audio_format = _audio_format_from_mime(mime_type)
    if audio_format in {"wav", "mp3", "ogg", "opus", "webm", "mp4", "flac", "aac"}:
        return f"audio.{audio_format}"
    return "audio.bin"
