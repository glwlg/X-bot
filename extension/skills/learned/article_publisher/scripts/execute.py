from __future__ import annotations

import argparse
import ast
import asyncio
import html
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import httpx
from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from core.config import get_client_for_model
from core.model_config import get_current_model
from extension.skills.builtin.credential_manager.scripts.store import get_credential
from services.openai_adapter import generate_text
from services.web_summary_service import fetch_webpage_content

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_RESULT_COUNT = 8
MAX_DEEP_READ_URLS = 3
MAX_SEARCH_CONTEXT_CHARS = 5000
MAX_DOC_SNIPPET_CHARS = 1200
MAX_LOCAL_MATERIAL_TOTAL_CHARS = 30000
MAX_LOCAL_MATERIAL_FILE_CHARS = 18000
SUPPORTED_LOCAL_MATERIAL_SUFFIXES = {".md", ".markdown", ".txt"}
MAX_SOCIAL_CONTEXT_CHARS = 6000
AUTHOR_ACCOUNT_KEYS = (
    "author",
    "auther",
    "article_author",
)
SUPPORTED_PUBLISH_CHANNELS = ("wechat", "xiaohongshu")


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _resolve_topic(
    ctx: UnifiedContext,
    params: dict[str, Any],
    *,
    fallback_paths: list[Path] | None = None,
) -> str:
    candidates = [
        params.get("topic"),
        params.get("query"),
        params.get("instruction"),
        getattr(getattr(ctx, "message", None), "text", ""),
    ]
    for value in candidates:
        topic = str(value or "").strip()
        if topic:
            return topic
    if fallback_paths:
        if len(fallback_paths) == 1:
            fallback_name = str(fallback_paths[0].stem or "").strip()
            if fallback_name:
                return fallback_name
        return "本地素材整理"
    return ""


def _parse_article_json(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("empty model response")

    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = ast.literal_eval(text)

    if isinstance(data, list):
        if not data or not isinstance(data[0], dict):
            raise ValueError("model returned list but first item is not an object")
        data = data[0]

    if not isinstance(data, dict):
        raise ValueError(f"model returned non-object type: {type(data).__name__}")
    return data


def _normalize_article_data(data: dict[str, Any], topic: str) -> dict[str, Any]:
    title = str(data.get("title") or f"{topic} 深度观察").strip()
    author = str(data.get("author") or "").strip()
    digest = str(data.get("digest") or "本文基于公开信息整理生成。").strip()

    cover_prompt = data.get("cover_prompt")
    if cover_prompt is not None:
        cover_prompt = str(cover_prompt).strip() or None

    sections_raw = data.get("sections")
    sections: list[dict[str, Any]] = []
    if isinstance(sections_raw, list):
        for sec in sections_raw:
            if not isinstance(sec, dict):
                continue
            content = sec.get("content")
            if not isinstance(content, str) or not content.strip():
                continue

            image_prompt = sec.get("image_prompt")
            if image_prompt is not None:
                image_prompt = str(image_prompt).strip() or None

            sections.append({"content": content, "image_prompt": image_prompt})

    if not sections:
        sections = [
            {"content": "<p>暂无正文内容，请稍后重试。</p>", "image_prompt": None}
        ]

    return {
        "title": title,
        "author": author,
        "digest": digest,
        "cover_prompt": cover_prompt,
        "sections": sections,
    }


def _decode_text_file(payload: Any) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="ignore")
    return str(payload or "")


def _resolve_local_material_paths(params: dict[str, Any]) -> list[Path]:
    raw_values: list[Any] = []
    for key in ("source_path", "source_paths", "material_path", "material_paths"):
        value = params.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            raw_values.extend(list(value))
        else:
            raw_values.append(value)

    resolved: list[Path] = []
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        if path not in resolved:
            resolved.append(path)
    return resolved


def _read_local_material_context(material_paths: list[Path]) -> str:
    if not material_paths:
        return ""

    docs: list[str] = []
    remaining = MAX_LOCAL_MATERIAL_TOTAL_CHARS
    for path in material_paths:
        if not path.exists() or not path.is_file():
            raise ValueError(f"本地素材不存在：{path}")
        if path.suffix.lower() not in SUPPORTED_LOCAL_MATERIAL_SUFFIXES:
            raise ValueError(
                f"本地素材格式不支持：{path.name}，仅支持 {', '.join(sorted(SUPPORTED_LOCAL_MATERIAL_SUFFIXES))}"
            )
        raw_text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw_text:
            raise ValueError(f"本地素材为空：{path}")

        allowed = min(remaining, MAX_LOCAL_MATERIAL_FILE_CHARS)
        if allowed <= 0:
            break
        snippet = raw_text[:allowed].strip()
        if not snippet:
            continue
        if len(raw_text) > len(snippet):
            snippet += "\n\n[内容过长，已截断]"
        docs.append(f"Local material: {path}\n{snippet}")
        remaining -= len(snippet)
        if remaining <= 0:
            break

    if not docs:
        raise ValueError("本地素材无法读取有效文本内容")
    return "\n\n---\n\n".join(docs)


def _normalize_publish_channel(value: Any) -> str:
    token = str(value or "").strip().lower()
    aliases = {
        "wechat": "wechat",
        "weixin": "wechat",
        "wechat_official_account": "wechat",
        "公众号": "wechat",
        "微信公众号": "wechat",
        "xiaohongshu": "xiaohongshu",
        "xhs": "xiaohongshu",
        "rednote": "xiaohongshu",
        "小红书": "xiaohongshu",
    }
    return aliases.get(token, "")


def _resolve_publish_channels(params: dict[str, Any]) -> list[str]:
    raw_values: list[Any] = []
    for key in ("publish_channel", "publish_channels", "channel", "channels"):
        value = params.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            raw_values.extend(list(value))
        else:
            raw_values.append(value)

    channels: list[str] = []
    for raw in raw_values:
        parts = re.split(r"[\s,，]+", str(raw or "").strip())
        for part in parts:
            normalized = _normalize_publish_channel(part)
            if normalized and normalized not in channels:
                channels.append(normalized)

    if not channels and _as_bool(params.get("publish"), default=False):
        channels.append("wechat")
    return channels


def _primary_author_account(
    accounts: dict[str, dict[str, Any] | None],
    publish_channels: list[str],
) -> dict[str, Any] | None:
    for channel in publish_channels:
        account = accounts.get(channel)
        if isinstance(account, dict):
            return account
    for channel in SUPPORTED_PUBLISH_CHANNELS:
        account = accounts.get(channel)
        if isinstance(account, dict):
            return account
    return None


def _resolve_article_author(
    account: dict[str, Any] | None,
    *,
    fallback_author: str = "",
) -> str:
    if isinstance(account, dict):
        for key in AUTHOR_ACCOUNT_KEYS:
            value = str(account.get(key) or "").strip()
            if value:
                return value
    resolved = str(fallback_author or "").strip()
    return resolved or "Ikaros"


def _author_watermark(author: str) -> str:
    safe_author = str(author or "").strip().lstrip("@")
    return f"@{safe_author or 'Ikaros'}"


def _augment_image_prompt(prompt: str, author: str) -> str:
    safe_watermark = _author_watermark(author)
    instructions = [
        "vector illustration",
        "flat style",
        "tech vibe",
        "high quality",
        "4k",
        f'include exactly one subtle creator watermark text "{safe_watermark}"',
        "place the watermark in a bottom corner",
        "do not include any other text",
        "no logo",
        "no signature",
        "no copyright mark",
        "no extra watermark",
        "no publisher name",
    ]
    return f"{prompt}, " + ", ".join(instructions)


def _html_to_plain_text(content: str) -> str:
    text = str(content or "")
    replacements = (
        (r"(?i)<br\s*/?>", "\n"),
        (r"(?i)</p>", "\n\n"),
        (r"(?i)</h[1-6]>", "\n\n"),
        (r"(?i)<li[^>]*>", "- "),
        (r"(?i)</li>", "\n"),
        (r"(?i)</blockquote>", "\n\n"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _article_plain_text(article_data: dict[str, Any]) -> str:
    parts: list[str] = []
    digest = str(article_data.get("digest") or "").strip()
    if digest:
        parts.append(digest)
    for section in list(article_data.get("sections") or []):
        plain = _html_to_plain_text(str(section.get("content") or ""))
        if plain:
            parts.append(plain)
    return "\n\n".join(parts).strip()


def _normalize_xiaohongshu_tags(raw_tags: Any, topic: str) -> list[str]:
    tokens: list[str] = []
    if isinstance(raw_tags, str):
        tokens.extend(re.split(r"[#\s,，]+", raw_tags))
    elif isinstance(raw_tags, list):
        for item in raw_tags:
            tokens.extend(re.split(r"[#\s,，]+", str(item or "")))

    normalized: list[str] = []
    for token in tokens:
        cleaned = str(token or "").strip().lstrip("#")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned[:24])
    if not normalized and str(topic or "").strip():
        normalized.append(str(topic).strip()[:24])
    return normalized[:8]


def _fallback_xiaohongshu_note(topic: str, article_data: dict[str, Any]) -> dict[str, Any]:
    title = str(article_data.get("title") or topic or "小红书笔记").strip()
    title = title[:20].rstrip()
    body = _article_plain_text(article_data)
    if not body:
        body = str(article_data.get("digest") or topic or "").strip()
    return {
        "title": title or "小红书笔记",
        "body": body,
        "tags": _normalize_xiaohongshu_tags([], topic or title),
    }


def _normalize_xiaohongshu_note_data(
    data: dict[str, Any],
    *,
    topic: str,
    article_data: dict[str, Any],
) -> dict[str, Any]:
    fallback = _fallback_xiaohongshu_note(topic, article_data)
    title = str(data.get("title") or fallback["title"]).strip() or fallback["title"]
    title = title[:20].rstrip() or fallback["title"]
    body = _html_to_plain_text(str(data.get("body") or fallback["body"])).strip()
    if not body:
        body = fallback["body"]
    tags = _normalize_xiaohongshu_tags(data.get("tags"), topic or title)
    if not tags:
        tags = list(fallback["tags"])
    return {"title": title, "body": body, "tags": tags}


async def _generate_xiaohongshu_note_json(
    topic: str,
    article_data: dict[str, Any],
) -> dict[str, Any]:
    article_plain = _article_plain_text(article_data)
    note_prompt = (
        f"你是一位擅长科技内容运营的小红书编辑，请把下面的文章整理成一篇适合小红书图文发布的笔记。\n"
        f"主题：{topic}\n"
        f"文章标题：{article_data.get('title') or topic}\n"
        f"文章正文：\n{article_plain[:MAX_SOCIAL_CONTEXT_CHARS]}\n\n"
        "要求：\n"
        "1. 返回严格 JSON。\n"
        '2. JSON 格式：{"title":"...","body":"...","tags":["标签1","标签2"]}。\n'
        "3. title 控制在 20 个汉字以内，避免夸张标题党。\n"
        "4. body 使用纯文本和换行，不要 Markdown，不要 HTML。\n"
        "5. tags 返回 3 到 8 个，不带 #。\n"
        "6. 仅返回 JSON 对象，不要额外解释。"
    )

    model_to_use = get_current_model()
    if not model_to_use:
        raise RuntimeError("No text model configured in config/models.json")
    async_client = get_client_for_model(model_to_use, is_async=True)
    if async_client is None:
        raise RuntimeError("OpenAI async client is not initialized")

    response_text = await generate_text(
        async_client=async_client,
        model=model_to_use,
        contents=note_prompt,
        config={"response_mime_type": "application/json"},
    )
    payload = _parse_article_json(str(response_text or ""))
    return _normalize_xiaohongshu_note_data(
        payload,
        topic=topic,
        article_data=article_data,
    )


def _build_xiaohongshu_note_attachment(note_data: dict[str, Any]) -> bytes:
    tags = " ".join(f"#{tag}" for tag in list(note_data.get("tags") or []) if str(tag).strip())
    lines = [
        f"标题：{str(note_data.get('title') or '').strip()}",
        "",
        "正文：",
        str(note_data.get("body") or "").strip(),
    ]
    if tags:
        lines.extend(["", "标签：", tags])
    return ("\n".join(lines).strip() + "\n").encode("utf-8")


def _extract_urls(text: str) -> list[str]:
    candidates: list[str] = []

    for match in re.findall(r"\((https?://[^)\s]+)\)", text or ""):
        candidates.append(match)
    for match in re.findall(r"https?://[^\s)\]\">]+", text or ""):
        candidates.append(match)

    urls: list[str] = []
    for raw in candidates:
        url = str(raw or "").strip().rstrip(".,;")
        if url and url not in urls:
            urls.append(url)
    return urls


def _extract_search_summary(search_res: Any) -> tuple[str, str]:
    if not isinstance(search_res, dict):
        return str(search_res or ""), ""

    text = str(search_res.get("text") or "")
    files = search_res.get("files")
    report_text = ""
    if isinstance(files, dict) and files.get("search_report.md") is not None:
        report_text = _decode_text_file(files.get("search_report.md"))
    return text, report_text


async def _collect_search_context(
    ctx: UnifiedContext,
    *,
    topic: str,
) -> dict[str, Any]:
    try:
        search_res = await ctx.run_skill(
            "web_search",
            {"query": topic, "num_results": DEFAULT_SEARCH_RESULT_COUNT},
        )
    except Exception as exc:
        logger.warning("web_search skill call failed: %s", exc)
        return {"summary_text": "", "report_text": "", "urls": []}

    summary_text, report_text = _extract_search_summary(search_res)
    url_source = report_text or summary_text
    unique_urls = _extract_urls(url_source)[:MAX_DEEP_READ_URLS]

    if unique_urls:
        return {
            "summary_text": summary_text,
            "report_text": report_text,
            "urls": unique_urls,
        }

    return {
        "summary_text": summary_text,
        "report_text": report_text,
        "urls": [],
    }


async def _generate_article_json(topic: str, search_context: str) -> dict[str, Any]:
    structure_prompt = (
        f"你是一位顶级科技媒体的主笔（风格类似'机器之心'或'36氪'），请基于以下素材为主题'{topic}'撰写一篇深度文章。\n"
        f"素材内容：\n{search_context[:MAX_SEARCH_CONTEXT_CHARS]}\n\n"
        f"**要求**：\n"
        f"1. 观点犀利，拒绝平铺直叙，要有深度分析和情感共鸣。\n"
        f"2. 必须设计 1 张封面图 PROMPT 和 1-2 张正文插图 PROMPT。\n"
        f"3. 正文内容要使用 HTML 标签 (`<p>`, `<h2>`, `<blockquote>`, `<ul>`, `<b>`) 排版，不要用 Markdown。\n"
        f"4. 返回严格 JSON 格式。\n"
        f"5. 仅返回 JSON 对象本身，不要 ```json 包裹，不要解释性文字。\n"
        f"6. JSON 必须使用双引号，示例：\n"
        f"{{\n"
        f'  "title": "震惊体或深度体标题",\n'
        f'  "author": "笔名",\n'
        f'  "digest": "120字摘要",\n'
        f'  "cover_prompt": "English prompt for cover image (16:9)",\n'
        f'  "sections": [\n'
        f'    {{ "content": "<p>第一部分文字...</p>", "image_prompt": "Optional English prompt for inline image (16:9) or null" }},\n'
        f'    {{ "content": "<h2>小标题</h2><p>第二部分文字...</p>", "image_prompt": null }}\n'
        f"  ]\n"
        f"}}"
    )

    model_to_use = get_current_model()
    if not model_to_use:
        raise RuntimeError("No text model configured in config/models.json")
    async_client = get_client_for_model(model_to_use, is_async=True)
    if async_client is None:
        raise RuntimeError("OpenAI async client is not initialized")

    response_text = await generate_text(
        async_client=async_client,
        model=model_to_use,
        contents=structure_prompt,
        config={"response_mime_type": "application/json"},
    )
    return _normalize_article_data(
        _parse_article_json(str(response_text or "")),
        topic,
    )


async def _generate_images(
    ctx: UnifiedContext,
    article_data: dict[str, Any],
    *,
    author: str,
) -> tuple[bytes | None, dict[int, bytes], dict[str, bytes]]:
    image_tasks: list[tuple[str, int, str]] = []

    if article_data.get("cover_prompt"):
        image_tasks.append(("cover", -1, str(article_data["cover_prompt"])))

    sections = list(article_data.get("sections") or [])
    for idx, sec in enumerate(sections):
        prompt = sec.get("image_prompt")
        if prompt:
            image_tasks.append(("section", idx, str(prompt)))

    if not image_tasks:
        return None, {}, {}

    async def gen_img(task: tuple[str, int, str]) -> tuple[str, int, bytes | None]:
        image_type, image_idx, prompt = task
        try:
            full_prompt = _augment_image_prompt(prompt, author)
            result = await ctx.run_skill(
                "generate_image",
                {"prompt": full_prompt, "aspect_ratio": "16:9"},
            )
            if isinstance(result, dict) and isinstance(result.get("files"), dict):
                files = list(result["files"].values())
                if files:
                    return image_type, image_idx, files[0]
        except Exception as exc:
            logger.warning(
                "Image generation failed for %s:%s: %s",
                image_type,
                image_idx,
                exc,
            )
        return image_type, image_idx, None

    img_results = await asyncio.gather(*(gen_img(task) for task in image_tasks))

    cover_bytes: bytes | None = None
    section_images: dict[int, bytes] = {}
    generated_files: dict[str, bytes] = {}

    for result_type, result_idx, result_bytes in img_results:
        if not result_bytes:
            continue

        file_name = f"img_{result_type}_{result_idx}.png"
        generated_files[file_name] = result_bytes

        if result_type == "cover":
            cover_bytes = result_bytes
        else:
            section_images[result_idx] = result_bytes

    return cover_bytes, section_images, generated_files


def _build_article_preview(
    article_data: dict[str, Any],
    *,
    cover_bytes: bytes | None,
    section_images: dict[int, bytes],
    publish: bool,
) -> str:
    preview = f"# {article_data['title']}\n*By {article_data['author']}*\n\n"
    preview += f"> {article_data['digest']}\n\n"

    if cover_bytes:
        preview += "![Cover](img_cover_-1.png)\n\n"

    if not publish:
        for idx, sec in enumerate(article_data["sections"]):
            preview += f"{sec.get('content', '')}\n\n"
            if idx in section_images:
                preview += f"![Image {idx}](img_section_{idx}.png)\n\n"

    return preview.strip()


async def _publish_to_wechat(
    *,
    publisher: "WeChatPublisher",
    article_data: dict[str, Any],
    cover_bytes: bytes | None,
    section_images: dict[int, bytes],
) -> str:
    thumb_media_id = None
    if cover_bytes:
        thumb_media_id = await publisher.upload_cover_image(cover_bytes)

    full_html = ""
    for idx, sec in enumerate(article_data["sections"]):
        full_html += str(sec.get("content", ""))
        if idx not in section_images:
            continue
        try:
            image_url = await publisher.upload_article_image(section_images[idx])
            full_html += f'<p><img src="{image_url}"/></p>'
        except Exception as exc:
            logger.error("Failed to upload inline image %s: %s", idx, exc)

    if not thumb_media_id:
        return "❌ 发布中止：封面图生成或上传失败。"

    digest_text = str(article_data.get("digest") or "")
    if len(digest_text) > 50:
        digest_text = digest_text[:50] + "..."
    if not full_html:
        full_html = "<p>Empty content.</p>"

    draft_id = await publisher.add_draft(
        title=article_data["title"],
        content_html=full_html,
        thumb_media_id=thumb_media_id,
        author=article_data["author"],
        digest=digest_text,
    )
    return f"✅ 已发布到公众号草稿箱，MediaID: `{draft_id}`"


def _format_wechat_publish_preflight_error(exc: Exception) -> str:
    raw = str(exc or "").strip()
    errcode_match = re.search(r"'errcode':\s*(\d+)", raw)
    ip_match = re.search(r"invalid ip\s+([0-9a-fA-F:\.\-]+)", raw, flags=re.IGNORECASE)
    errcode = errcode_match.group(1) if errcode_match else ""
    ip = ip_match.group(1) if ip_match else ""

    if errcode == "40164":
        details = "当前服务器出口 IP 不在微信公众号白名单中"
        if ip:
            details += f"：`{ip}`"
        return (
            "❌ 发布前检查失败："
            f"{details}。\n"
            "请先把该 IP 加入公众号后台白名单，再重新执行发布。"
        )
    return f"❌ 发布前检查失败：{raw or '无法获取公众号 access token'}"


async def _prepare_wechat_publisher(
    account: dict[str, Any] | None,
) -> tuple["WeChatPublisher | None", str]:
    if not account:
        return None, "⚠️ 发布中止：未配置公众号凭证 `wechat_official_account`。"

    app_id = account.get("app_id") if isinstance(account, dict) else None
    app_secret = account.get("app_secret") if isinstance(account, dict) else None
    if not app_id or not app_secret:
        return None, "⚠️ 发布中止：公众号凭证缺少 `app_id` 或 `app_secret`。"

    publisher = WeChatPublisher(str(app_id), str(app_secret))
    try:
        await publisher.get_access_token()
    except Exception as exc:
        logger.error("WeChat publish preflight failed: %s", exc, exc_info=True)
        return None, _format_wechat_publish_preflight_error(exc)
    return publisher, ""


async def _run_subprocess(
    args: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(
            f"Command timed out after {int(timeout_seconds)}s: {' '.join(args[:4])}"
        ) from None
    return (
        int(proc.returncode or 0),
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
    )


def _condense_command_output(text: str, *, limit: int = 300) -> str:
    condensed = re.sub(r"\s+", " ", str(text or "").strip())
    if len(condensed) <= limit:
        return condensed
    return condensed[: limit - 3].rstrip() + "..."


def _resolve_opencli_path() -> str:
    configured = str(os.getenv("OPENCLI_BIN") or "").strip()
    if configured:
        return configured
    return shutil.which("opencli") or ""


def _sanitize_opencli_publish_cmd(cmd: list[str]) -> str:
    sanitized: list[str] = []
    idx = 0
    while idx < len(cmd):
        token = str(cmd[idx] or "")
        if idx == 3:
            sanitized.append(f"<content:{len(token)} chars>")
        elif token == "--images" and idx + 1 < len(cmd):
            image_names = [
                Path(item).name
                for item in str(cmd[idx + 1] or "").split(",")
                if str(item or "").strip()
            ]
            sanitized.extend([token, ",".join(image_names)])
            idx += 1
        else:
            sanitized.append(token)
        idx += 1
    return " ".join(sanitized)


def _load_json_from_mixed_text(text: str) -> Any | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for candidate in reversed(lines):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for idx, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            payload, _end = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            continue
        return payload
    return None


def _format_opencli_publish_error(
    *,
    code: int | None = None,
    status: str = "",
    detail: str = "",
    stdout: str = "",
    stderr: str = "",
) -> str:
    parts: list[str] = []
    if status:
        parts.append(f"status={status}")
    if detail:
        parts.append(detail)
    if code not in (None, 0):
        parts.append(f"exit={code}")

    stdout_summary = _condense_command_output(stdout)
    stderr_summary = _condense_command_output(stderr)
    if stderr_summary:
        parts.append(f"stderr={stderr_summary}")
    if stdout_summary and stdout_summary not in parts and stdout_summary != detail:
        parts.append(f"stdout={stdout_summary}")

    return "; ".join(part for part in parts if part) or "unknown opencli publish error"


def _opencli_doctor_connected(output_text: str) -> bool:
    normalized = str(output_text or "").strip().lower()
    return (
        "[ok] extension: connected" in normalized
        and "[ok] connectivity: connected" in normalized
    )


def _opencli_has_xiaohongshu_session(output_text: str) -> bool:
    normalized = str(output_text or "").strip().lower()
    return "site:xiaohongshu" in normalized


def _format_opencli_command_error(
    *,
    action: str,
    code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    detail: str = "",
) -> str:
    parts: list[str] = [action]
    if detail:
        parts.append(detail)
    if code not in (None, 0):
        parts.append(f"exit={code}")
    stderr_summary = _condense_command_output(stderr)
    stdout_summary = _condense_command_output(stdout)
    if stderr_summary:
        parts.append(f"stderr={stderr_summary}")
    elif stdout_summary:
        parts.append(f"stdout={stdout_summary}")
    return "; ".join(part for part in parts if part)


async def _ensure_xiaohongshu_opencli_session(
    opencli_path: str,
    *,
    phase: str,
    force_warmup: bool,
) -> str:
    try:
        doctor_code, doctor_stdout, doctor_stderr = await _run_subprocess(
            [opencli_path, "doctor", "--sessions"],
            timeout_seconds=20.0,
        )
    except Exception as exc:
        return f"⚠️ 发布中止：{phase}无法执行 `opencli doctor --sessions`：{exc}"

    doctor_text = "\n".join(
        part for part in (doctor_stdout, doctor_stderr) if str(part or "").strip()
    ).strip()
    doctor_summary = _condense_command_output(doctor_text) or "unknown doctor output"

    if doctor_code != 0:
        return (
            f"⚠️ 发布中止：{phase}检测到 opencli 会话异常："
            + _format_opencli_command_error(
                action="doctor --sessions",
                code=doctor_code,
                stdout=doctor_stdout,
                stderr=doctor_stderr,
            )
        )

    if not _opencli_doctor_connected(doctor_text):
        return (
            f"⚠️ 发布中止：{phase}检测到 opencli bridge 未连接：{doctor_summary}"
        )

    if not force_warmup and _opencli_has_xiaohongshu_session(doctor_text):
        return ""

    warmup_errors: list[str] = []
    for attempt in range(2):
        cmd = [opencli_path, "xiaohongshu", "creator-profile", "-f", "json"]
        logger.info(
            "opencli xiaohongshu warmup attempt %s starting (%s): %s",
            attempt + 1,
            phase,
            " ".join(cmd),
        )
        code, stdout, stderr = await _run_subprocess(cmd, timeout_seconds=90.0)
        logger.info(
            "opencli xiaohongshu warmup attempt %s finished (%s): code=%s stdout=%s stderr=%s",
            attempt + 1,
            phase,
            code,
            _condense_command_output(stdout) or "<empty>",
            _condense_command_output(stderr) or "<empty>",
        )
        if code == 0 and str(stdout or "").strip():
            return ""

        warmup_errors.append(
            _format_opencli_command_error(
                action=f"creator-profile warmup {attempt + 1}",
                code=code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        if attempt == 0:
            await asyncio.sleep(2)

    return (
        f"⚠️ 发布中止：{phase}小红书会话预热失败："
        + "；".join(warmup_errors)
        + f"；doctor={doctor_summary}"
    )


async def _prepare_xiaohongshu_opencli() -> str:
    opencli_path = _resolve_opencli_path()
    if not opencli_path:
        return "⚠️ 发布中止：未找到 `opencli` 命令，请先安装并确保它在 PATH 中。"
    try:
        code, _, stderr = await _run_subprocess(
            [opencli_path, "xiaohongshu", "publish", "--help"],
            timeout_seconds=15.0,
        )
    except Exception as exc:
        return f"⚠️ 发布中止：无法执行 `opencli xiaohongshu publish --help`：{exc}"
    if code != 0:
        detail = _condense_command_output(stderr) or "未知错误"
        return f"⚠️ 发布中止：`opencli` 小红书发布子命令不可用：{detail}"
    return await _ensure_xiaohongshu_opencli_session(
        opencli_path,
        phase="预检阶段",
        force_warmup=False,
    )


class WeChatPublisher:
    BASE_URL = "https://api.weixin.qq.com/cgi-bin"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token: str | None = None
        self.token_expiry = 0.0

    async def get_access_token(self) -> str:
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token

        url = f"{self.BASE_URL}/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.app_id,
            "secret": self.app_secret,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if "access_token" not in data:
                raise RuntimeError(f"Failed to get access token: {data}")
            self.access_token = data["access_token"]
            self.token_expiry = time.time() + data.get("expires_in", 7200) - 200
            return self.access_token

    async def upload_cover_image(
        self,
        image_bytes: bytes,
        filename: str = "cover.png",
    ) -> str:
        token = await self.get_access_token()
        url = f"{self.BASE_URL}/material/add_material?access_token={token}&type=image"
        files = {"media": (filename, image_bytes, "image/png")}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, files=files)
            resp.raise_for_status()
            data = resp.json()
            if "media_id" not in data:
                raise RuntimeError(f"Failed to upload cover: {data}")
            return str(data["media_id"])

    async def upload_article_image(
        self,
        image_bytes: bytes,
        filename: str = "image.png",
    ) -> str:
        token = await self.get_access_token()
        url = f"{self.BASE_URL}/media/uploadimg?access_token={token}"
        files = {"media": (filename, image_bytes, "image/png")}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, files=files)
            resp.raise_for_status()
            data = resp.json()
            if "url" not in data:
                raise RuntimeError(f"Failed to upload article image: {data}")
            return str(data["url"])

    async def add_draft(
        self,
        *,
        title: str,
        content_html: str,
        thumb_media_id: str,
        author: str = "Ikaros",
        digest: str = "",
    ) -> str:
        token = await self.get_access_token()
        url = f"{self.BASE_URL}/draft/add?access_token={token}"
        payload = {
            "articles": [
                {
                    "title": title,
                    "author": author,
                    "digest": digest,
                    "content": content_html,
                    "content_source_url": "",
                    "thumb_media_id": thumb_media_id,
                    "need_open_comment": 1,
                    "only_fans_can_comment": 0,
                }
            ]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "media_id" in data:
                return str(data["media_id"])
            if data.get("errcode") == 0:
                return "success"
            raise RuntimeError(f"Failed to add draft: {data}")


def _extract_opencli_result_row(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if any(
            key in payload
            for key in ("status", "detail", "message", "note_id", "draft_id", "url")
        ):
            return payload
        for key in ("data", "result", "rows", "items"):
            nested = _extract_opencli_result_row(payload.get(key))
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _extract_opencli_result_row(item)
            if nested:
                return nested
    return {}


def _parse_opencli_publish_output(stdout_text: str) -> dict[str, Any]:
    text = str(stdout_text or "").strip()
    if not text:
        return {}
    payload = _load_json_from_mixed_text(text)
    if payload is None:
        return {"detail": text}
    return _extract_opencli_result_row(payload) or {"detail": text}


async def _publish_to_xiaohongshu(
    *,
    topic: str,
    note_data: dict[str, Any],
    cover_bytes: bytes | None,
    section_images: dict[int, bytes],
) -> str:
    opencli_path = _resolve_opencli_path()
    if not opencli_path:
        return "❌ 小红书发布中止：未找到 `opencli` 命令。"

    session_error = await _ensure_xiaohongshu_opencli_session(
        opencli_path,
        phase="发布前",
        force_warmup=True,
    )
    if session_error:
        raise RuntimeError(session_error.removeprefix("⚠️ 发布中止：").strip())

    publish_images: list[tuple[str, bytes]] = []
    if cover_bytes:
        publish_images.append(("cover.png", cover_bytes))
    for idx, image_bytes in sorted(section_images.items()):
        if image_bytes:
            publish_images.append((f"section-{idx}.png", image_bytes))
    publish_images = publish_images[:9]
    if not publish_images:
        return "❌ 小红书发布中止：至少需要 1 张配图。"

    note_body = str(note_data.get("body") or "").strip()
    title = str(note_data.get("title") or "").strip()[:20].rstrip()
    topics = [
        str(tag or "").strip().lstrip("#")
        for tag in list(note_data.get("tags") or [])
        if str(tag or "").strip()
    ]
    if not topics and str(topic or "").strip():
        topics = [str(topic).strip()[:24]]

    async def _run_opencli_publish_once(cmd: list[str], *, attempt: int) -> str:
        safe_cmd = _sanitize_opencli_publish_cmd(cmd)
        logger.info(
            "opencli xiaohongshu publish attempt %s starting: %s",
            attempt,
            safe_cmd,
        )
        code, stdout, stderr = await _run_subprocess(cmd, timeout_seconds=300.0)
        logger.info(
            "opencli xiaohongshu publish attempt %s finished: code=%s stdout=%s stderr=%s",
            attempt,
            code,
            _condense_command_output(stdout) or "<empty>",
            _condense_command_output(stderr) or "<empty>",
        )
        if code != 0:
            raise RuntimeError(
                _format_opencli_publish_error(
                    code=code,
                    stdout=stdout,
                    stderr=stderr,
                )
            )

        result = _parse_opencli_publish_output(stdout)
        status = str(result.get("status") or "").strip().lower()
        detail = _condense_command_output(
            str(result.get("detail") or result.get("message") or stdout)
        )
        if status in {"error", "failed", "failure"}:
            raise RuntimeError(
                _format_opencli_publish_error(
                    status=status,
                    detail=detail,
                    stdout=stdout,
                    stderr=stderr,
                )
            )
        return detail

    with tempfile.TemporaryDirectory(prefix="ikaros-xhs-") as temp_dir:
        image_paths: list[str] = []
        for filename, image_bytes in publish_images:
            target_path = Path(temp_dir) / filename
            target_path.write_bytes(image_bytes)
            image_paths.append(str(target_path))

        cmd = [
            opencli_path,
            "xiaohongshu",
            "publish",
            note_body,
            "--format",
            "json",
        ]
        if title:
            cmd.extend(["--title", title])
        if image_paths:
            cmd.extend(["--images", ",".join(image_paths)])
        if topics:
            cmd.extend(["--topics", ",".join(topics[:8])])

        detail = ""
        retried = False
        attempt_errors: list[str] = []
        for attempt in range(2):
            attempt_cmd = list(cmd)
            if attempt == 1:
                attempt_cmd.append("--verbose")
            try:
                detail = await _run_opencli_publish_once(attempt_cmd, attempt=attempt + 1)
                break
            except Exception as exc:
                error_text = str(exc).strip() or "unknown error"
                attempt_errors.append(f"第{attempt + 1}次: {error_text}")
                if attempt == 0:
                    retried = True
                    logger.warning(
                        "opencli xiaohongshu publish failed on first attempt, retrying in 2s: %s",
                        error_text,
                    )
                    await asyncio.sleep(2)
                    continue
                logger.error(
                    "opencli xiaohongshu publish failed after retry: %s",
                    " | ".join(attempt_errors),
                )
                raise RuntimeError("；".join(attempt_errors))
        if not detail and attempt_errors:
            raise RuntimeError("；".join(attempt_errors))

    parts = ["✅ 已通过 opencli 提交到小红书"]
    if retried:
        parts.append("重试后成功")
    if detail:
        parts.append(detail)
    return "，".join(parts)


async def execute(ctx: UnifiedContext, params: dict[str, Any], runtime=None):
    _ = runtime
    material_paths = _resolve_local_material_paths(params)
    topic = _resolve_topic(ctx, params, fallback_paths=material_paths)
    publish = _as_bool(params.get("publish"), default=False)
    publish_channels = _resolve_publish_channels(params)
    publisher: WeChatPublisher | None = None
    wechat_account = await get_credential(ctx.message.user.id, "wechat_official_account")
    xiaohongshu_account = await get_credential(ctx.message.user.id, "xiaohongshu_publisher")
    account_map = {
        "wechat": wechat_account,
        "xiaohongshu": xiaohongshu_account,
    }

    if not topic:
        yield {
            "ok": False,
            "failure_mode": "recoverable",
            "text": "❌ 请提供文章主题。",
            "ui": {},
        }
        return

    local_material_context = ""
    if material_paths:
        yield f"📄 正在读取 {len(material_paths)} 份本地素材..."
        try:
            local_material_context = _read_local_material_context(material_paths)
        except Exception as exc:
            yield {
                "ok": False,
                "failure_mode": "recoverable",
                "text": f"❌ 本地素材读取失败: {exc}",
                "ui": {},
            }
            return

    if publish:
        if "wechat" in publish_channels:
            yield "🔐 正在检查公众号发布权限与 IP 白名单..."
            publisher, preflight_error = await _prepare_wechat_publisher(wechat_account)
            if preflight_error:
                yield {
                    "ok": False,
                    "failure_mode": "fatal",
                    "text": preflight_error,
                    "ui": {},
                    "terminal": True,
                    "task_outcome": "failed",
                }
                return
        if "xiaohongshu" in publish_channels:
            yield "🔐 正在检查 opencli 小红书发布能力..."
            preflight_error = await _prepare_xiaohongshu_opencli()
            if preflight_error:
                yield {
                    "ok": False,
                    "failure_mode": "fatal",
                    "text": preflight_error,
                    "ui": {},
                    "terminal": True,
                    "task_outcome": "failed",
                }
                return

    search_context = ""
    if local_material_context:
        yield "🧾 正在基于本地素材整理写作上下文..."
        search_context = local_material_context
    else:
        yield f"🔍 正在全网搜索 `{topic}` 深度资料..."
        search_payload = await _collect_search_context(ctx, topic=topic)

        deep_read_urls = list(search_payload.get("urls") or [])
        if deep_read_urls:
            yield f"📖 正在深度阅读 {len(deep_read_urls)} 篇核心讯息..."
            docs: list[str] = []
            for url in deep_read_urls:
                content = await fetch_webpage_content(url)
                if content:
                    docs.append(f"Src: {url}\n{content[:MAX_DOC_SNIPPET_CHARS]}")
            if docs:
                search_context = "\n---\n".join(docs)

        if not search_context:
            search_context = str(search_payload.get("report_text") or "").strip()
        if not search_context:
            search_context = str(search_payload.get("summary_text") or "").strip()

    yield "✍️ 正在构思文章结构与配图设计..."
    try:
        article_data = await _generate_article_json(topic, search_context)
    except Exception as exc:
        logger.error("Article generation failed: %s", exc, exc_info=True)
        yield {
            "ok": False,
            "failure_mode": "recoverable",
            "text": f"❌ 创作失败: {exc}",
            "ui": {},
        }
        return

    article_data["author"] = _resolve_article_author(
        _primary_author_account(account_map, publish_channels),
        fallback_author=str(article_data.get("author") or ""),
    )

    yield "🎨 正在并行绘制封面与插图..."
    cover_bytes, section_images, generated_files = await _generate_images(
        ctx,
        article_data,
        author=str(article_data.get("author") or ""),
    )

    preview_text = _build_article_preview(
        article_data,
        cover_bytes=cover_bytes,
        section_images=section_images,
        publish=publish,
    )
    final_text = f"🔇🔇🔇【文章内容】\n\n{preview_text}".strip()
    publish_statuses: list[str] = []

    xiaohongshu_note_data: dict[str, Any] | None = None
    if "xiaohongshu" in publish_channels:
        yield "📝 正在生成小红书笔记版本..."
        try:
            xiaohongshu_note_data = await _generate_xiaohongshu_note_json(topic, article_data)
        except Exception as exc:
            logger.warning("Xiaohongshu note generation failed, using fallback: %s", exc)
            xiaohongshu_note_data = _fallback_xiaohongshu_note(topic, article_data)
        generated_files["xiaohongshu_note.txt"] = _build_xiaohongshu_note_attachment(
            xiaohongshu_note_data
        )
        generated_files["xiaohongshu_note.json"] = json.dumps(
            xiaohongshu_note_data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        if not publish:
            publish_statuses.append("📝 已生成小红书发布草稿附件。")

    if publish and "wechat" in publish_channels:
        yield "📤 正在上传素材并同步至微信后台..."
        try:
            publish_statuses.append(
                await _publish_to_wechat(
                    publisher=publisher,
                    article_data=article_data,
                    cover_bytes=cover_bytes,
                    section_images=section_images,
                )
            )
        except Exception as exc:
            logger.error("WeChat publish failed: %s", exc, exc_info=True)
            publish_statuses.append(f"❌ 微信发布失败: {exc}")

    if publish and "xiaohongshu" in publish_channels and xiaohongshu_note_data is not None:
        yield "📤 正在调用 opencli 发布到小红书..."
        try:
            publish_statuses.append(
                await _publish_to_xiaohongshu(
                    topic=topic,
                    note_data=xiaohongshu_note_data,
                    cover_bytes=cover_bytes,
                    section_images=section_images,
                )
            )
        except Exception as exc:
            logger.error("Xiaohongshu publish failed: %s", exc, exc_info=True)
            publish_statuses.append(f"❌ 小红书发布失败: {exc}")

    if publish_statuses:
        final_text = f"{final_text}\n\n---\n" + "\n".join(publish_statuses)

    yield {
        "ok": True,
        "text": final_text,
        "files": generated_files,
        "ui": {},
        "task_outcome": "done",
        "terminal": True,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a long-form article and optional multi-channel publishing outputs.",
    )
    parser.add_argument(
        "topic",
        nargs="*",
        help="Article topic. If omitted, --message-text or ctx.message.text is used.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the generated content to selected channels.",
    )
    parser.add_argument(
        "--publish-channel",
        action="append",
        default=[],
        help="Publish/output channel. Supported: wechat, xiaohongshu. Can be passed multiple times.",
    )
    parser.add_argument(
        "--source-path",
        action="append",
        default=[],
        help="Local markdown/txt material path. Can be passed multiple times.",
    )
    add_common_arguments(parser)
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    topic = " ".join(str(item or "").strip() for item in list(args.topic or [])).strip()
    source_paths = [
        str(item or "").strip()
        for item in list(getattr(args, "source_path", []) or [])
        if str(item or "").strip()
    ]
    publish_channels = [
        str(item or "").strip()
        for item in list(getattr(args, "publish_channel", []) or [])
        if str(item or "").strip()
    ]
    explicit = {
        "topic": topic or None,
        "publish": bool(getattr(args, "publish", False)),
        "publish_channels": publish_channels or None,
        "source_paths": source_paths or None,
    }
    return merge_params(args, explicit)


async def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
