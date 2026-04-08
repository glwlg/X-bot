"""Article data structures, JSON parsing, and text utilities."""

from __future__ import annotations

import ast
import html
import json
import re
from pathlib import Path
from typing import Any

MAX_SEARCH_CONTEXT_CHARS = 5000
MAX_DOC_SNIPPET_CHARS = 1200
MAX_LOCAL_MATERIAL_TOTAL_CHARS = 30000
MAX_LOCAL_MATERIAL_FILE_CHARS = 18000
MAX_SOCIAL_CONTEXT_CHARS = 6000
SUPPORTED_LOCAL_MATERIAL_SUFFIXES = {".md", ".markdown", ".txt"}
AUTHOR_ACCOUNT_KEYS = ("author", "auther", "article_author")
SUPPORTED_PUBLISH_CHANNELS = ("wechat", "xiaohongshu")
NEWS_TOPIC_KEYWORDS = ("新闻", "快讯", "资讯", "时讯", "要闻", "报道", "动态")
SAME_DAY_NEWS_PATTERNS = (
    r"当天的?新闻",
    r"今天的?新闻",
    r"今日新闻",
    r"当日新闻",
    r"当天的?(?:快讯|资讯|动态|报道)",
    r"今天的?(?:快讯|资讯|动态|报道)",
    r"今日(?:快讯|资讯|动态|报道)",
    r"确保新闻是当天的新闻",
    r"只写当天新闻",
    r"只看当天新闻",
)
PUBLIC_READER_KEYWORDS = ("公众号", "读者", "公众", "面向公众", "面向读者")
BODY_ONLY_KEYWORDS = ("非正文", "只输出正文", "仅输出正文", "不要包含非正文", "不要写非正文")
IGNORED_FORBIDDEN_TERMS = {"子任务", "非正文", "正文", "内容", "公众号", "文章"}
NON_BODY_HTML_PATTERNS = (
    r"(?is)<p[^>]*>\s*(?:以下(?:是|为).{0,40}?|正文如下|下面进入正文|以下内容由.*?生成)\s*</p>",
    r"(?is)<p[^>]*>\s*(?:免责声明[:：]?.*?|责编[:：]?.*?|责任编辑[:：]?.*?|图片来源[:：]?.*?|封面来源[:：]?.*?|欢迎在评论区.*?|欢迎留言.*?|欢迎关注.*?|感谢阅读.*?|END)\s*</p>",
    r"(?is)<h[1-6][^>]*>\s*(?:以下是.*?|以下为.*?|正文如下)\s*</h[1-6]>",
)
FORCED_NEWS_FILLER_RULES = (
    (r"没有\s*新官宣", "没有新官宣"),
    (r"虽然\s*没有\s*官宣", "虽然没有官宣"),
    (r"值得关注(?:的)?是行业信号", "值得关注的是行业信号"),
)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def parse_article_json(raw_text: str) -> dict[str, Any]:
    """Parse a raw model response into a dict, tolerating markdown fences."""
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


def normalize_article_data(data: dict[str, Any], topic: str) -> dict[str, Any]:
    """Normalize a parsed article dict into a canonical structure."""
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
            content = strip_non_body_html(str(content))
            if not content:
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


# ---------------------------------------------------------------------------
# HTML / text utilities
# ---------------------------------------------------------------------------

def html_to_plain_text(content: str) -> str:
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


def strip_non_body_html(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    for pattern in NON_BODY_HTML_PATTERNS:
        text = re.sub(pattern, "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def article_plain_text(article_data: dict[str, Any]) -> str:
    parts: list[str] = []
    digest = str(article_data.get("digest") or "").strip()
    if digest:
        parts.append(digest)
    for section in list(article_data.get("sections") or []):
        plain = html_to_plain_text(str(section.get("content") or ""))
        if plain:
            parts.append(plain)
    return "\n\n".join(parts).strip()


def _clean_subject_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.strip("“”\"'《》<>[]（）() ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip("，,。；;：: ")


def extract_primary_subject(topic: str) -> str:
    raw = str(topic or "").strip()
    if not raw:
        return ""

    patterns = (
        r"(?:写|生成|整理|创作)(?:一篇)?关于(?P<subject>.+?)的(?:公众号|推文|长文|文章)",
        r"关于(?P<subject>.+?)的(?:公众号|推文|长文|文章)",
        r"围绕(?P<subject>.+?)(?:写|生成|整理|创作)(?:一篇)?(?:公众号|推文|长文|文章)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw)
        if not match:
            continue
        subject = _clean_subject_text(match.group("subject"))
        if subject:
            return subject

    cleaned = raw
    for pattern in (
        r"^用daily_query获取今天日期[，,、 ]*(?:然后)?",
        r"(?:然后)?用article_publisher技能",
        r"写好之后发布到公众号.*$",
        r"发布到公众号.*$",
        r"注意不要用子任务.*$",
    ):
        cleaned = re.sub(pattern, "", cleaned)
    return _clean_subject_text(cleaned)


def extract_forbidden_terms(topic: str) -> list[str]:
    raw = str(topic or "").strip()
    if not raw:
        return []

    patterns = (
        r"不要(?:用)?(?:涉及|提及|包含|写|出现)?(?P<terms>[^，。,；;\n]+?)(?:的内容|相关内容|相关报道|相关信息|相关素材|[，。,；;\n]|$)",
        r"避免(?:使用|提及|包含)?(?P<terms>[^，。,；;\n]+?)(?:[，。,；;\n]|$)",
        r"排除(?P<terms>[^，。,；;\n]+?)(?:[，。,；;\n]|$)",
    )

    forbidden_terms: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, raw):
            chunk = str(match.group("terms") or "").strip()
            chunk = re.sub(r"^(?:涉及|提及|包含|写|出现|关于|有关|相关|使用|用)", "", chunk)
            chunk = re.sub(r"(?:的内容|相关内容|相关报道|相关信息|相关素材)$", "", chunk)
            chunk = chunk.strip("“”\"' ")
            for part in re.split(r"[、,，/和及与]", chunk):
                token = part.strip()
                if len(token) < 2:
                    continue
                if token in IGNORED_FORBIDDEN_TERMS:
                    continue
                if token not in forbidden_terms:
                    forbidden_terms.append(token)
    return forbidden_terms


def derive_topic_requirements(topic: str, *, current_date: str = "") -> dict[str, Any]:
    raw = str(topic or "").strip()
    subject = extract_primary_subject(raw) or raw
    forbidden_terms = extract_forbidden_terms(raw)
    explicit_news_request = any(keyword in raw for keyword in NEWS_TOPIC_KEYWORDS)
    same_day_only = any(re.search(pattern, raw) for pattern in SAME_DAY_NEWS_PATTERNS)
    prefer_news = explicit_news_request or same_day_only
    public_readers = any(keyword in raw for keyword in PUBLIC_READER_KEYWORDS)
    body_only = any(keyword in raw for keyword in BODY_ONLY_KEYWORDS)

    search_parts = [subject]
    if same_day_only and current_date:
        search_parts.append(current_date)
    search_query = " ".join(part for part in search_parts if str(part).strip()).strip()

    return {
        "raw_topic": raw,
        "subject": subject,
        "search_query": search_query or raw,
        "explicit_news_request": explicit_news_request,
        "prefer_news": prefer_news,
        "same_day_only": same_day_only,
        "public_readers": public_readers,
        "body_only": body_only,
        "forbidden_terms": forbidden_terms,
        "current_date": str(current_date or "").strip(),
    }


def build_news_rejection_message(subject: str, *, same_day_only: bool) -> str:
    safe_subject = _clean_subject_text(subject) or "该主题"
    if same_day_only:
        return f"今日未发现足够支撑发布的【{safe_subject}】当天新闻，不建议发文。"
    return f"今日未发现足够支撑发布的【{safe_subject}】相关新闻素材，不建议发文。"


def detect_forced_news_fillers(text: str) -> list[str]:
    raw = str(text or "")
    if not raw:
        return []

    hits: list[str] = []
    for pattern, label in FORCED_NEWS_FILLER_RULES:
        if re.search(pattern, raw):
            hits.append(label)
    return hits


def filter_lines_by_forbidden_terms(text: str, forbidden_terms: list[str]) -> str:
    raw = str(text or "").strip()
    if not raw or not forbidden_terms:
        return raw
    lines = [
        line for line in raw.splitlines()
        if not any(term and term in line for term in forbidden_terms)
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------------------
# Image prompt helpers
# ---------------------------------------------------------------------------

def author_watermark(author: str) -> str:
    safe_author = str(author or "").strip().lstrip("@")
    return f"@{safe_author or 'Ikaros'}"


def augment_image_prompt(prompt: str, author: str) -> str:
    safe_watermark = author_watermark(author)
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


# ---------------------------------------------------------------------------
# Author resolution
# ---------------------------------------------------------------------------

def resolve_article_author(
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


def primary_author_account(
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


# ---------------------------------------------------------------------------
# Local material helpers
# ---------------------------------------------------------------------------

def resolve_local_material_paths(params: dict[str, Any]) -> list[Path]:
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


def read_local_material_context(material_paths: list[Path]) -> str:
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


# ---------------------------------------------------------------------------
# Channel helpers
# ---------------------------------------------------------------------------

def normalize_publish_channel(value: Any) -> str:
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


def resolve_publish_channels(params: dict[str, Any]) -> list[str]:
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
            normalized = normalize_publish_channel(part)
            if normalized and normalized not in channels:
                channels.append(normalized)

    if not channels and _as_bool(params.get("publish"), default=False):
        channels.append("wechat")
    return channels


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def as_bool(value: Any, *, default: bool = False) -> bool:
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


# keep internal alias
_as_bool = as_bool


def resolve_topic(
    ctx: Any,
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


def topic_slug(topic: str, *, max_len: int = 40) -> str:
    """Convert a topic string to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", topic.strip()).strip("_")
    return slug[:max_len] or "untitled"


def decode_text_file(payload: Any) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="ignore")
    return str(payload or "")


def extract_urls(text: str) -> list[str]:
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


def build_article_preview(
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
