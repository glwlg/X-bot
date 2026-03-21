from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from core.file_artifacts import extract_file_rows_from_text
from core.platform.models import MessageType, UnifiedContext
from core.state_store import get_video_cache
from services.image_input_service import (
    DEFAULT_MAX_IMAGE_INPUT_BYTES,
    fetch_image_from_url,
    guess_image_mime_type,
)
from services.web_summary_service import extract_urls, fetch_webpage_content

logger = logging.getLogger(__name__)

MAX_INLINE_IMAGE_INPUTS = 5


@dataclass
class ResolvedInlineInput:
    mime_type: str
    content: bytes
    source_kind: str
    source_ref: str


@dataclass
class InlineInputResolution:
    inputs: list[ResolvedInlineInput] = field(default_factory=list)
    detected_refs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ReplyMessageResolution:
    inputs: list[ResolvedInlineInput] = field(default_factory=list)
    extra_context: str = ""
    detected_refs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class PreparedAgentInput:
    message_history: list[dict[str, Any]] = field(default_factory=list)
    user_parts: list[dict[str, Any]] = field(default_factory=list)
    final_user_message: str = ""
    inline_inputs: list[ResolvedInlineInput] = field(default_factory=list)
    current_resolution: InlineInputResolution = field(default_factory=InlineInputResolution)
    reply_resolution: ReplyMessageResolution = field(default_factory=ReplyMessageResolution)
    detected_refs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    truncated_inline_count: int = 0
    has_inline_inputs: bool = False
    has_reply_media: bool = False
    extra_context: str = ""


@dataclass
class _InlineInputCandidate:
    start: int
    source_kind: str
    source_ref: str


def _append_unique_text(target: list[str], values: list[str]) -> None:
    for value in list(values or []):
        safe_value = str(value or "").strip()
        if safe_value and safe_value not in target:
            target.append(safe_value)


def _merge_inline_input_resolutions(
    primary: InlineInputResolution,
    secondary: InlineInputResolution,
) -> InlineInputResolution:
    merged = InlineInputResolution()
    seen_inputs: set[tuple[str, str]] = set()

    for input_item in list(primary.inputs or []) + list(secondary.inputs or []):
        key = (
            str(getattr(input_item, "source_kind", "") or "").strip(),
            str(getattr(input_item, "source_ref", "") or "").strip(),
        )
        if key in seen_inputs:
            continue
        seen_inputs.add(key)
        merged.inputs.append(input_item)

    _append_unique_text(merged.detected_refs, primary.detected_refs)
    _append_unique_text(merged.detected_refs, secondary.detected_refs)
    _append_unique_text(merged.errors, primary.errors)
    _append_unique_text(merged.errors, secondary.errors)
    return merged


def dedupe_inline_inputs(items: list[ResolvedInlineInput]) -> list[ResolvedInlineInput]:
    deduped: list[ResolvedInlineInput] = []
    seen: set[tuple[str, str]] = set()
    for item in list(items or []):
        key = (
            str(getattr(item, "source_kind", "") or "").strip(),
            str(getattr(item, "source_ref", "") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def strip_inline_input_refs(text: str, refs: list[str]) -> str:
    rendered = str(text or "")
    if not rendered:
        return ""

    cleaned = rendered
    for ref in list(refs or []):
        safe_ref = str(ref or "").strip()
        if not safe_ref:
            continue
        cleaned = cleaned.replace(safe_ref, " ")

    cleaned = re.sub(r"[\s,;:!?.`'\"(){}\[\]<>|/\\]+", " ", cleaned).strip()
    return cleaned


def default_inline_input_prompt(image_count: int) -> str:
    return "请结合这些图片回答" if int(image_count or 0) > 1 else "请分析这张图片"


def inline_input_to_part(input_item: ResolvedInlineInput) -> dict[str, Any] | None:
    content = bytes(getattr(input_item, "content", b"") or b"")
    if not content:
        return None
    mime_type = (
        str(getattr(input_item, "mime_type", "") or "").strip()
        or "application/octet-stream"
    )
    return {
        "inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(content).decode("utf-8"),
        }
    }


def _extract_reply_urls(reply_to: object) -> list[str]:
    reply_urls: list[str] = []

    if hasattr(reply_to, "entities") and reply_to.entities:
        try:
            for entity in reply_to.entities:
                if entity.type == "text_link":
                    reply_urls.append(entity.url)
                elif entity.type == "url" and hasattr(reply_to, "parse_entity"):
                    reply_urls.append(reply_to.parse_entity(entity))
        except Exception as exc:
            logger.warning("Error parsing reply entities: %s", exc)

    if hasattr(reply_to, "caption_entities") and reply_to.caption_entities:
        try:
            for entity in reply_to.caption_entities:
                if entity.type == "text_link":
                    reply_urls.append(entity.url)
                elif entity.type == "url" and hasattr(reply_to, "parse_caption_entity"):
                    reply_urls.append(reply_to.parse_caption_entity(entity))
        except Exception as exc:
            logger.warning("Error parsing reply caption entities: %s", exc)

    reply_text = str(
        getattr(reply_to, "text", "") or getattr(reply_to, "caption", "") or ""
    )
    for url in extract_urls(reply_text):
        reply_urls.append(url)

    unique_urls: list[str] = []
    _append_unique_text(unique_urls, reply_urls)
    return unique_urls


def _build_inline_input(
    *,
    mime_type: str,
    content: bytes,
    source_kind: str,
    source_ref: str,
) -> ResolvedInlineInput:
    return ResolvedInlineInput(
        mime_type=str(mime_type or "").strip(),
        content=bytes(content or b""),
        source_kind=str(source_kind or "").strip(),
        source_ref=str(source_ref or "").strip(),
    )


def _sorted_inline_candidates(text: str, *, limit_hint: int) -> list[_InlineInputCandidate]:
    raw_text = str(text or "")
    if not raw_text:
        return []

    candidates: list[_InlineInputCandidate] = []
    search_start = 0
    for url in extract_urls(raw_text):
        start = raw_text.find(url, search_start)
        if start >= 0:
            search_start = start + len(url)
        else:
            start = len(raw_text) + len(candidates)
        candidates.append(
            _InlineInputCandidate(
                start=start,
                source_kind="url",
                source_ref=str(url),
            )
        )

    file_rows = extract_file_rows_from_text(
        raw_text,
        max_size_bytes=DEFAULT_MAX_IMAGE_INPUT_BYTES,
        limit=max(max(1, int(limit_hint)), 16),
    )
    for index, row in enumerate(file_rows):
        if str(row.get("kind") or "").strip().lower() != "photo":
            continue
        path_text = str(row.get("path") or "").strip()
        if not path_text:
            continue
        start = raw_text.find(path_text)
        if start < 0:
            start = len(raw_text) + len(candidates) + index
        candidates.append(
            _InlineInputCandidate(
                start=start,
                source_kind="local_path",
                source_ref=path_text,
            )
        )

    candidates.sort(
        key=lambda item: (int(item.start), str(item.source_kind), str(item.source_ref))
    )
    return candidates


async def _resolve_inline_candidate(
    candidate: _InlineInputCandidate,
) -> ResolvedInlineInput:
    if candidate.source_kind == "url":
        content, mime_type = await fetch_image_from_url(candidate.source_ref)
        return _build_inline_input(
            mime_type=mime_type,
            content=content,
            source_kind="url",
            source_ref=candidate.source_ref,
        )

    if candidate.source_kind == "local_path":
        path_text = str(candidate.source_ref or "").strip()
        try:
            with open(path_text, "rb") as file_obj:
                content = file_obj.read()
        except Exception as exc:
            raise RuntimeError("本地图片无法读取") from exc

        if not content:
            raise RuntimeError("本地图片内容为空")
        if len(content) > DEFAULT_MAX_IMAGE_INPUT_BYTES:
            raise RuntimeError("本地图片超过大小限制")

        mime_type = guess_image_mime_type(content, source_name=path_text)
        if not mime_type.startswith("image/"):
            raise RuntimeError("本地文件不是图片")
        return _build_inline_input(
            mime_type=mime_type,
            content=content,
            source_kind="local_path",
            source_ref=path_text,
        )

    raise RuntimeError(f"unsupported inline input candidate: {candidate.source_kind}")


async def resolve_inline_inputs_from_urls(
    urls: list[str],
    *,
    limit: int = MAX_INLINE_IMAGE_INPUTS,
) -> InlineInputResolution:
    resolution = InlineInputResolution()
    seen_refs: set[str] = set()

    for url in list(urls or []):
        safe_url = str(url or "").strip()
        if not safe_url or safe_url in seen_refs:
            continue
        seen_refs.add(safe_url)
        resolution.detected_refs.append(safe_url)
        try:
            resolution.inputs.append(
                await _resolve_inline_candidate(
                    _InlineInputCandidate(
                        start=len(resolution.inputs),
                        source_kind="url",
                        source_ref=safe_url,
                    )
                )
            )
        except Exception as exc:
            logger.info("Failed to resolve inline image URL %s: %s", safe_url, exc)
            resolution.errors.append(safe_url)
        if len(resolution.inputs) >= max(1, int(limit)):
            break

    return resolution


async def resolve_inline_inputs_from_text(
    text: str,
    *,
    limit: int = MAX_INLINE_IMAGE_INPUTS,
) -> InlineInputResolution:
    resolution = InlineInputResolution()
    seen_refs: set[str] = set()
    candidates = _sorted_inline_candidates(text, limit_hint=limit * 3)

    for candidate in candidates:
        safe_ref = str(candidate.source_ref or "").strip()
        if not safe_ref or safe_ref in seen_refs:
            continue
        seen_refs.add(safe_ref)
        resolution.detected_refs.append(safe_ref)
        try:
            resolution.inputs.append(await _resolve_inline_candidate(candidate))
        except Exception as exc:
            logger.info(
                "Failed to resolve inline input kind=%s ref=%s err=%s",
                candidate.source_kind,
                safe_ref,
                exc,
            )
            resolution.errors.append(safe_ref)
        if len(resolution.inputs) >= max(1, int(limit)):
            break

    return resolution


async def resolve_inline_inputs_from_texts(
    texts: list[str],
    *,
    limit: int = MAX_INLINE_IMAGE_INPUTS,
) -> InlineInputResolution:
    merged = InlineInputResolution()
    for text in list(texts or []):
        safe_text = str(text or "")
        if not safe_text.strip():
            continue
        partial = await resolve_inline_inputs_from_text(
            safe_text,
            limit=max(1, int(limit)),
        )
        merged = _merge_inline_input_resolutions(merged, partial)
        if len(merged.inputs) >= max(1, int(limit)):
            merged.inputs = list(merged.inputs[: max(1, int(limit))])
            break
    return merged


async def process_reply_message(ctx: UnifiedContext) -> ReplyMessageResolution:
    reply_to = ctx.message.reply_to_message
    if not reply_to:
        return ReplyMessageResolution()

    result = ReplyMessageResolution()
    reply_text = str(reply_to.text or reply_to.caption or "")
    logger.info(
        "Checking reply_to message %s for inline inputs", getattr(reply_to, "id", "")
    )

    if reply_text:
        text_resolution = await resolve_inline_inputs_from_text(reply_text)
        _append_unique_text(result.detected_refs, text_resolution.detected_refs)
        _append_unique_text(result.errors, text_resolution.errors)
        result.inputs.extend(text_resolution.inputs)

    reply_urls = _extract_reply_urls(reply_to)
    missing_urls = [url for url in reply_urls if url not in set(result.detected_refs)]
    if missing_urls:
        url_resolution = await resolve_inline_inputs_from_urls(missing_urls)
        merged = _merge_inline_input_resolutions(
            InlineInputResolution(
                inputs=list(result.inputs),
                detected_refs=list(result.detected_refs),
                errors=list(result.errors),
            ),
            url_resolution,
        )
        result.inputs = list(merged.inputs)
        result.detected_refs = list(merged.detected_refs)
        result.errors = list(merged.errors)

    if reply_urls and not result.inputs:
        status_msg = await ctx.reply("📄 正在获取引用网页内容...")
        await ctx.send_chat_action(action="typing")
        try:
            web_content = await fetch_webpage_content(reply_urls[0])
            if web_content:
                result.extra_context = f"【引用网页内容】\n{web_content}\n\n"
            else:
                result.extra_context = (
                    "【系统提示】引用的网页链接无法访问（无法提取内容，可能是反爬虫限制）。"
                    "请在回答中明确告知用户你无法读取该链接的内容，并仅根据现有的文本信息进行回答。"
                    "\n\n"
                )
            await status_msg.delete()
        except Exception as exc:
            logger.error("Error fetching reply URL: %s", exc)
            result.extra_context = (
                "【系统提示】读取链接时发生错误。请告知用户无法访问该链接。\n\n"
            )
            await status_msg.delete()
    elif not reply_urls and reply_text:
        if len(reply_text) > 2000:
            reply_text = reply_text[:2000] + "...(省略)"
        result.extra_context = f"【用户引用的消息】\n{reply_text}\n\n"
        logger.info("Extracted reply text context: %s chars", len(reply_text))

    if reply_to.type == MessageType.VIDEO:
        file_id = reply_to.file_id
        mime_type = reply_to.mime_type or "video/mp4"

        cache_path = await get_video_cache(file_id)
        if cache_path and os.path.exists(cache_path):
            logger.info("Using cached video: %s", cache_path)
            await ctx.reply("🎬 正在分析视频（使用缓存）...")
            with open(cache_path, "rb") as file_obj:
                result.inputs.append(
                    _build_inline_input(
                        mime_type=mime_type,
                        content=bytes(file_obj.read()),
                        source_kind="reply_media",
                        source_ref=str(file_id),
                    )
                )

        if not any(
            item.source_kind == "reply_media" and item.source_ref == str(file_id)
            for item in result.inputs
        ):
            if reply_to.file_size and reply_to.file_size > 20 * 1024 * 1024:
                await ctx.reply(
                    "⚠️ 引用的视频文件过大（超过 20MB），无法通过 Telegram 下载分析。\n\n"
                    "提示：Bot 下载的视频会被缓存，可以直接分析。"
                )
                return result

            await ctx.reply("🎬 正在下载并分析视频...")
            result.inputs.append(
                _build_inline_input(
                    mime_type=mime_type,
                    content=bytes(await ctx.download_file(file_id)),
                    source_kind="reply_media",
                    source_ref=str(file_id),
                )
            )

    elif reply_to.type == MessageType.IMAGE:
        mime_type = reply_to.mime_type or "image/jpeg"
        await ctx.reply("🔍 正在分析图片...")
        result.inputs.append(
            _build_inline_input(
                mime_type=mime_type,
                content=bytes(await ctx.download_file(reply_to.file_id)),
                source_kind="reply_media",
                source_ref=str(reply_to.file_id),
            )
        )

    elif reply_to.type in (MessageType.AUDIO, MessageType.VOICE):
        file_id = reply_to.file_id
        mime_type = reply_to.mime_type

        if reply_to.type == MessageType.AUDIO:
            if not mime_type:
                mime_type = "audio/mpeg"
            label = "音频"
        else:
            if not mime_type:
                mime_type = "audio/ogg"
            label = "语音"

        file_size = reply_to.file_size
        if file_size and file_size > 20 * 1024 * 1024:
            await ctx.reply(
                f"⚠️ 引用的{label}文件过大（超过 20MB），无法通过 Telegram 下载分析。"
            )
            return result

        await ctx.reply(f"🎧 正在分析{label}...")
        result.inputs.append(
            _build_inline_input(
                mime_type=mime_type,
                content=bytes(await ctx.download_file(file_id)),
                source_kind="reply_media",
                source_ref=str(file_id),
            )
        )

    return result


async def build_agent_message_history(
    ctx: UnifiedContext,
    *,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    include_reply: bool = False,
    inline_input_source_texts: list[str] | None = None,
    strip_refs_from_user_message: bool = True,
    max_inline_inputs: int = MAX_INLINE_IMAGE_INPUTS,
) -> PreparedAgentInput:
    prepared = PreparedAgentInput()
    prepared.current_resolution = await resolve_inline_inputs_from_texts(
        list(inline_input_source_texts or [user_message]),
        limit=max_inline_inputs,
    )
    if include_reply:
        prepared.reply_resolution = await process_reply_message(ctx)

    if prepared.reply_resolution.extra_context:
        prepared.extra_context = prepared.reply_resolution.extra_context

    combined_inline_inputs = dedupe_inline_inputs(
        list(prepared.current_resolution.inputs or [])
        + list(prepared.reply_resolution.inputs or [])
    )
    prepared.truncated_inline_count = max(
        0, len(combined_inline_inputs) - max_inline_inputs
    )
    if prepared.truncated_inline_count:
        combined_inline_inputs = combined_inline_inputs[:max_inline_inputs]
    prepared.inline_inputs = list(combined_inline_inputs)

    detected_refs: list[str] = []
    errors: list[str] = []
    _append_unique_text(detected_refs, prepared.current_resolution.detected_refs)
    _append_unique_text(detected_refs, prepared.reply_resolution.detected_refs)
    _append_unique_text(errors, prepared.current_resolution.errors)
    _append_unique_text(errors, prepared.reply_resolution.errors)
    prepared.detected_refs = detected_refs
    prepared.errors = errors

    prepared.has_inline_inputs = bool(prepared.inline_inputs)
    prepared.has_reply_media = any(
        str(getattr(item, "source_kind", "") or "").strip() == "reply_media"
        for item in list(prepared.reply_resolution.inputs or [])
    )

    final_user_message = str(user_message or "")
    if strip_refs_from_user_message and prepared.current_resolution.detected_refs:
        stripped_inline_refs = strip_inline_input_refs(
            final_user_message,
            list(prepared.current_resolution.detected_refs),
        )
        if not stripped_inline_refs and prepared.has_inline_inputs:
            image_count = sum(
                1
                for item in list(prepared.inline_inputs)
                if str(getattr(item, "mime_type", "") or "").startswith("image/")
            )
            final_user_message = default_inline_input_prompt(image_count or 1)

    if prepared.extra_context:
        final_user_message = prepared.extra_context + "用户请求：" + final_user_message

    user_parts: list[dict[str, Any]] = [{"text": final_user_message}]
    for inline_input in list(prepared.inline_inputs):
        part = inline_input_to_part(inline_input)
        if part is not None:
            user_parts.append(part)

    prepared.final_user_message = final_user_message
    prepared.user_parts = user_parts
    prepared.message_history = list(history or [])
    prepared.message_history.append({"role": "user", "parts": user_parts})
    return prepared
