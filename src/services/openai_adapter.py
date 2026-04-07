from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any


_AUDIO_PART_STYLE = str(os.getenv("OPENAI_AUDIO_PART_STYLE") or "file").strip().lower()
_VIDEO_PART_STYLE = str(
    os.getenv("OPENAI_VIDEO_PART_STYLE") or "video_url"
).strip().lower()
_TEXT_CONTENT_PART_TYPES = {"text", "output_text", "input_text"}


def build_messages(
    *,
    contents: Any,
    system_instruction: str | None = None,
    config: dict[str, Any] | None = None,
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

        content_blocks = _build_content_blocks(parts, config=config)
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
    from core.model_config import get_model_id_for_api

    cfg = config if isinstance(config, dict) else {}
    kwargs = {
        "model": get_model_id_for_api(model),
        "messages": build_messages(
            contents=contents,
            system_instruction=str(cfg.get("system_instruction") or "") or None,
            config=cfg,
        ),
    }
    return apply_generation_config(kwargs=kwargs, config=cfg)


def prepare_chat_completion_kwargs(
    kwargs: dict[str, Any],
    *,
    session_id: Any | None = None,
) -> dict[str, Any]:
    request_kwargs = dict(kwargs or {})
    request_kwargs["stream"] = True

    upstream_session_id = _resolve_upstream_session_id(session_id)
    if upstream_session_id:
        request_kwargs["user"] = upstream_session_id
        extra_body = request_kwargs.get("extra_body")
        if isinstance(extra_body, dict):
            merged_extra_body = dict(extra_body)
        else:
            merged_extra_body = {}
        merged_extra_body["session_id"] = upstream_session_id
        request_kwargs["extra_body"] = merged_extra_body

    return request_kwargs


async def create_chat_completion(
    *,
    async_client: Any,
    session_id: Any | None = None,
    **kwargs: Any,
) -> Any:
    request_kwargs = prepare_chat_completion_kwargs(kwargs, session_id=session_id)
    response = await async_client.chat.completions.create(**request_kwargs)
    return await collect_chat_completion_response(response)


def create_chat_completion_sync(
    *,
    sync_client: Any,
    session_id: Any | None = None,
    **kwargs: Any,
) -> Any:
    request_kwargs = prepare_chat_completion_kwargs(kwargs, session_id=session_id)
    response = sync_client.chat.completions.create(**request_kwargs)
    return collect_chat_completion_response_sync(response)


async def generate_text(
    *,
    async_client: Any,
    model: str,
    contents: Any,
    config: dict[str, Any] | None = None,
) -> str:
    kwargs = build_chat_kwargs(model=model, contents=contents, config=config)
    response = await create_chat_completion(async_client=async_client, **kwargs)
    return extract_text_from_chat_completion(response)


def generate_text_sync(
    *,
    sync_client: Any,
    model: str,
    contents: Any,
    config: dict[str, Any] | None = None,
) -> str:
    kwargs = build_chat_kwargs(model=model, contents=contents, config=config)
    response = create_chat_completion_sync(sync_client=sync_client, **kwargs)
    return extract_text_from_chat_completion(response)


def is_async_chat_completion_stream(response: Any) -> bool:
    return hasattr(response, "__aiter__")


def is_sync_chat_completion_stream(response: Any) -> bool:
    if is_async_chat_completion_stream(response):
        return False
    if isinstance(response, (str, bytes, bytearray, dict, list, tuple, set)):
        return False
    return hasattr(response, "__iter__")


async def collect_chat_completion_response(response: Any) -> Any:
    if not is_async_chat_completion_stream(response):
        return response

    chunks: list[Any] = []
    async for chunk in response:
        chunks.append(chunk)
    return build_chat_completion_from_stream_chunks(chunks)


def collect_chat_completion_response_sync(response: Any) -> Any:
    if not is_sync_chat_completion_stream(response):
        return response

    chunks = list(response)
    return build_chat_completion_from_stream_chunks(chunks)


def build_chat_completion_from_stream_chunks(chunks: list[Any]) -> Any:
    text_chunks: list[str] = []
    refusal_chunks: list[str] = []
    tool_calls: dict[int, dict[str, str]] = {}
    response_id = ""
    response_model = ""
    response_object = ""
    finish_reason = ""
    usage: Any = None

    for chunk in chunks:
        if chunk is None:
            continue
        if not response_id:
            response_id = str(_read_key_or_attr(chunk, "id") or "").strip()
        if not response_model:
            response_model = str(_read_key_or_attr(chunk, "model") or "").strip()
        if not response_object:
            response_object = str(_read_key_or_attr(chunk, "object") or "").strip()

        chunk_usage = _read_key_or_attr(chunk, "usage")
        if chunk_usage is not None:
            usage = chunk_usage

        choices = list(_read_key_or_attr(chunk, "choices") or [])
        if not choices:
            continue

        choice = choices[0]
        current_finish_reason = str(
            _read_key_or_attr(choice, "finish_reason") or ""
        ).strip()
        if current_finish_reason:
            finish_reason = current_finish_reason

        delta = _read_key_or_attr(choice, "delta")
        message = _read_key_or_attr(choice, "message")
        if delta is not None:
            text = extract_text_from_chat_completion_stream_delta(delta)
            refusal = _extract_text_value(_read_key_or_attr(delta, "refusal"))
            stream_tool_calls = _read_key_or_attr(delta, "tool_calls") or []
        else:
            text = _extract_text_from_content(
                _read_key_or_attr(message, "content"),
                strip=False,
            )
            refusal = _extract_text_value(_read_key_or_attr(message, "refusal"))
            stream_tool_calls = _read_key_or_attr(message, "tool_calls") or []

        if text:
            text_chunks.append(text)
        if refusal:
            refusal_chunks.append(refusal)
        _merge_stream_tool_calls(tool_calls, stream_tool_calls)

    content = "".join(text_chunks).strip()
    refusal_text = "".join(refusal_chunks).strip()
    merged_tool_calls = [
        SimpleNamespace(
            id=str(item.get("id") or "").strip(),
            function=SimpleNamespace(
                name=str(item.get("name") or "").strip(),
                arguments=str(item.get("arguments") or ""),
            ),
        )
        for _, item in sorted(tool_calls.items(), key=lambda entry: entry[0])
        if str(item.get("name") or "").strip()
    ]

    return SimpleNamespace(
        id=response_id or None,
        model=response_model or None,
        object=response_object or "chat.completion",
        text=content or None,
        usage=usage,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason or None,
                message=SimpleNamespace(
                    content=content,
                    tool_calls=merged_tool_calls,
                    refusal=refusal_text or None,
                ),
            )
        ],
    )


def _resolve_upstream_session_id(session_id: Any | None = None) -> str:
    raw = session_id
    if raw is None:
        try:
            from core.llm_usage_store import current_llm_usage_session_id

            raw = current_llm_usage_session_id()
        except Exception:
            raw = ""
    return str(raw or "").strip()


def _merge_stream_tool_calls(
    aggregated: dict[int, dict[str, str]],
    stream_tool_calls: Any,
) -> None:
    for index, raw_call in enumerate(list(stream_tool_calls or [])):
        if raw_call is None:
            continue
        raw_index = _read_key_or_attr(raw_call, "index")
        try:
            call_index = int(raw_index) if raw_index is not None else index
        except Exception:
            call_index = index

        existing = aggregated.setdefault(
            call_index,
            {"id": "", "name": "", "arguments": ""},
        )
        call_id = str(_read_key_or_attr(raw_call, "id") or "").strip()
        if call_id:
            existing["id"] = call_id

        function = _read_key_or_attr(raw_call, "function")
        name = str(_read_key_or_attr(function, "name") or "").strip()
        if name:
            existing["name"] = name

        arguments = _read_key_or_attr(function, "arguments")
        if arguments is None:
            continue
        if isinstance(arguments, str):
            existing["arguments"] += arguments
            continue
        serialized = _extract_text_value(arguments)
        if serialized:
            existing["arguments"] += serialized


def extract_text_from_chat_completion(response: Any) -> str:
    direct_text = _extract_text_value(
        _read_key_or_attr(response, "text") or _read_key_or_attr(response, "output_text")
    )
    if direct_text:
        return direct_text

    choices = list(_read_key_or_attr(response, "choices") or [])
    if choices:
        message = _read_key_or_attr(choices[0], "message")
        content_text = _extract_text_from_content(
            _read_key_or_attr(message, "content")
        )
        if content_text:
            return content_text
        refusal_text = _extract_text_value(_read_key_or_attr(message, "refusal"))
        if refusal_text:
            return refusal_text

    candidates = _read_key_or_attr(response, "candidates") or []
    for candidate in candidates:
        content = _read_key_or_attr(candidate, "content")
        parts = _read_key_or_attr(content, "parts") or []
        content_text = _extract_text_from_content(parts)
        if content_text:
            return content_text
    return ""


def extract_text_from_chat_completion_stream_delta(delta: Any) -> str:
    if delta is None:
        return ""
    content = _read_key_or_attr(delta, "content")
    if isinstance(content, str):
        return content
    return _extract_text_from_content(content, strip=False)


def _extract_text_from_content(content: Any, *, strip: bool = True) -> str:
    if isinstance(content, str):
        return content.strip() if strip else content
    if not isinstance(content, (list, tuple)):
        return ""

    chunks: list[str] = []
    for item in content:
        text = _extract_text_from_content_part(item)
        if text:
            chunks.append(text)

    merged = "".join(chunks)
    return merged.strip() if strip else merged


def _extract_text_from_content_part(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part

    part_type = _read_type(part)
    if part_type and part_type not in _TEXT_CONTENT_PART_TYPES:
        return ""

    for candidate in (
        _read_key_or_attr(part, "text"),
        _read_key_or_attr(part, "value"),
        _read_key_or_attr(part, "content"),
    ):
        text = _extract_text_value(candidate)
        if text:
            return text

    return ""


def _extract_text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return _extract_text_from_content(value)

    for candidate in (
        _read_key_or_attr(value, "value"),
        _read_key_or_attr(value, "text"),
        _read_key_or_attr(value, "content"),
    ):
        if candidate is value:
            continue
        text = _extract_text_value(candidate)
        if text:
            return text
    return ""


def _read_type(payload: Any) -> str:
    raw_type = _read_key_or_attr(payload, "type")
    return str(raw_type or "").strip().lower()


def _read_key_or_attr(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    try:
        return getattr(payload, key, None)
    except Exception:
        return None


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


def _build_content_blocks(
    parts: Any,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    cfg = config if isinstance(config, dict) else {}
    audio_part_style = str(
        cfg.get("audio_part_style") or _AUDIO_PART_STYLE or "file"
    ).strip().lower()
    video_part_style = str(
        cfg.get("video_part_style") or _VIDEO_PART_STYLE or "video_url"
    ).strip().lower()
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
            if audio_part_style == "input_audio":
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
            if audio_part_style == "input_audio_data_uri":
                blocks.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:{mime_type};base64,{raw_data}",
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

        if mime_type.startswith("video/"):
            data_url = f"data:{mime_type};base64,{raw_data}"
            if video_part_style == "video":
                blocks.append(
                    {
                        "type": "video",
                        "video": {"url": data_url},
                    }
                )
                continue

            blocks.append(
                {
                    "type": "video_url",
                    "video_url": {"url": data_url},
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
