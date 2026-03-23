from __future__ import annotations

import argparse
import ast
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
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
from core.state_store import get_account
from services.openai_adapter import generate_text
from services.web_summary_service import fetch_webpage_content

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_RESULT_COUNT = 8
MAX_DEEP_READ_URLS = 3
MAX_SEARCH_CONTEXT_CHARS = 5000
MAX_DOC_SNIPPET_CHARS = 1200
AUTHOR_ACCOUNT_KEYS = (
    "author",
    "auther",
    "article_author",
)


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


def _resolve_topic(ctx: UnifiedContext, params: dict[str, Any]) -> str:
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
    return resolved or "X-Bot"


def _author_watermark(author: str) -> str:
    safe_author = str(author or "").strip().lstrip("@")
    return f"@{safe_author or 'X-Bot'}"


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
        author: str = "X-Bot",
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


async def execute(ctx: UnifiedContext, params: dict[str, Any], runtime=None):
    _ = runtime
    topic = _resolve_topic(ctx, params)
    publish = _as_bool(params.get("publish"), default=False)
    publisher: WeChatPublisher | None = None
    account = await get_account(ctx.message.user.id, "wechat_official_account")

    if not topic:
        yield {
            "ok": False,
            "failure_mode": "recoverable",
            "text": "❌ 请提供文章主题。",
            "ui": {},
        }
        return

    if publish:
        yield "🔐 正在检查公众号发布权限与 IP 白名单..."
        publisher, preflight_error = await _prepare_wechat_publisher(account)
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

    yield f"🔍 正在全网搜索 `{topic}` 深度资料..."
    search_context = ""
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
        account,
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
    final_text = f"🔇🔇🔇【新闻文章】\n\n{preview_text}".strip()

    if publish:
        yield "📤 正在上传素材并同步至微信后台..."
        try:
            publish_status = await _publish_to_wechat(
                publisher=publisher,
                article_data=article_data,
                cover_bytes=cover_bytes,
                section_images=section_images,
            )
        except Exception as exc:
            logger.error("Publish failed: %s", exc, exc_info=True)
            publish_status = f"❌ 发布失败: {exc}"
        if publish_status:
            final_text = f"{final_text}\n\n---\n{publish_status}"

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
        description="Generate a long-form news article and optional WeChat draft.",
    )
    parser.add_argument(
        "topic",
        nargs="*",
        help="Article topic. If omitted, --message-text or ctx.message.text is used.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the generated article to WeChat draft box.",
    )
    add_common_arguments(parser)
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    topic = " ".join(str(item or "").strip() for item in list(args.topic or [])).strip()
    explicit = {
        "topic": topic or None,
        "publish": bool(getattr(args, "publish", False)),
    }
    return merge_params(args, explicit)


async def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
