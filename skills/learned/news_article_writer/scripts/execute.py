import logging
import json
import re
import asyncio
import httpx
import time
import ast
from typing import Dict, Any, List

from core.platform.models import UnifiedContext
from services.web_summary_service import fetch_webpage_content
from core.config import gemini_client, GEMINI_MODEL
from repositories.account_repo import get_account

logger = logging.getLogger(__name__)


def _parse_article_json(raw_text: str) -> Dict[str, Any]:
    """Parse model output JSON with a safe fallback for python-style dict text."""
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("empty model response")

    # Strip fenced code block wrappers if model returns them.
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback for single-quote pseudo-JSON.
        data = ast.literal_eval(text)

    if isinstance(data, list):
        if not data or not isinstance(data[0], dict):
            raise ValueError("model returned list but first item is not an object")
        data = data[0]

    if not isinstance(data, dict):
        raise ValueError(f"model returned non-object type: {type(data).__name__}")
    return data


def _normalize_article_data(data: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """Normalize model output into a stable structure to avoid runtime KeyError/TypeError."""
    title = str(data.get("title") or f"{topic} 深度观察").strip()
    author = str(data.get("author") or "X-Bot").strip()
    digest = str(data.get("digest") or "本文基于公开信息整理生成。").strip()

    cover_prompt = data.get("cover_prompt")
    if cover_prompt is not None:
        cover_prompt = str(cover_prompt).strip() or None

    sections_raw = data.get("sections")
    sections: List[Dict[str, Any]] = []
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
        sections = [{"content": "<p>暂无正文内容，请稍后重试。</p>", "image_prompt": None}]

    return {
        "title": title,
        "author": author,
        "digest": digest,
        "cover_prompt": cover_prompt,
        "sections": sections,
    }


class WeChatPublisher:
    """WeChat Official Account Publisher Helper"""

    BASE_URL = "https://api.weixin.qq.com/cgi-bin"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = None
        self.token_expiry = 0

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
                raise Exception(f"Failed to get access token: {data}")
            self.access_token = data["access_token"]
            self.token_expiry = time.time() + data.get("expires_in", 7200) - 200
            return self.access_token

    async def upload_cover_image(
        self, image_bytes: bytes, filename: str = "cover.png"
    ) -> str:
        """Upload image to permanent material (for Cover) -> media_id"""
        token = await self.get_access_token()
        url = f"{self.BASE_URL}/material/add_material?access_token={token}&type=image"
        files = {"media": (filename, image_bytes, "image/png")}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, files=files)
            resp.raise_for_status()
            data = resp.json()
            if "media_id" not in data:
                raise Exception(f"Failed to upload cover: {data}")
            return data["media_id"]

    async def upload_article_image(
        self, image_bytes: bytes, filename: str = "image.png"
    ) -> str:
        """Upload image for article body -> url"""
        token = await self.get_access_token()
        url = f"{self.BASE_URL}/media/uploadimg?access_token={token}"
        files = {"media": (filename, image_bytes, "image/png")}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, files=files)
            resp.raise_for_status()
            data = resp.json()
            if "url" not in data:
                raise Exception(f"Failed to upload article image: {data}")
            return data["url"]

    async def add_draft(
        self,
        title: str,
        content_html: str,
        thumb_media_id: str,
        author: str = "X-Bot",
        digest: str = "",
    ) -> str:
        """Add draft to WeChat"""
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
            # Ensure UTF-8 json
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "media_id" in data:
                return data["media_id"]
            if data.get("errcode") == 0:
                return "success"
            raise Exception(f"Failed to add draft: {data}")


async def execute(ctx: UnifiedContext, params: dict) -> Dict[str, Any]:
    topic = params.get("topic")
    publish = params.get("publish", False)

    if not topic:
        yield "❌ 请提供文章主题。"
        return

    # 1. 搜索与素材收集
    yield f"🔍 正在全网搜索 `{topic}` 深度资料..."

    search_context = ""
    try:
        from core.skill_loader import skill_loader

        if skill_loader.get_skill("searxng_search"):
            # Increase limit for better breadth
            search_res = await ctx.run_skill(
                "searxng_search", {"query": topic, "num_results": 8}
            )
            search_summary_text = (
                search_res.get("text", "") if isinstance(search_res, dict) else str(search_res)
            )
            if not search_summary_text and search_res is not None:
                search_summary_text = str(search_res)
            search_context = search_summary_text

            # Extract URLs and fetch deep content
            urls = re.findall(r'(https?://[^\s\)\]"]+)', search_summary_text)
            unique_urls = list(dict.fromkeys(urls))[:3]

            if unique_urls:
                yield f"📖 正在深度阅读 {len(unique_urls)} 篇核心讯息..."
                docs = []
                for url in unique_urls:
                    c = await fetch_webpage_content(url)
                    if c:
                        docs.append(f"Src: {url}\n{c[:1200]}")
                if docs:
                    search_context = "\n---\n".join(docs)
    except Exception as e:
        logger.warning(f"Search failed: {e}")

    # 2. 结构化创作
    yield "✍️ 正在构思文章结构与配图设计..."

    structure_prompt = (
        f"你是一位顶级科技媒体的主笔（风格类似'机器之心'或'36氪'），请基于以下素材为主题'{topic}'撰写一篇深度文章。\n"
        f"素材内容：\n{search_context[:5000]}\n\n"
        f"**要求**：\n"
        f"1. 观点犀利，拒绝平铺直叙，要有深度分析和情感共鸣。\n"
        f"2. 必须设计 1 张封面图 PROMPT 和 1-2 张正文插图 PROMPT。\n"
        f"3. 正文内容要使用 HTML 标签 (`<p>`, `<h2>`, `<blockquote>`, `<ul>`, `<b>`) 排版，不要用 Markdown。\n"
        f"4. 返回严格 JSON 格式：\n"
        f"5. 仅返回 JSON 对象本身，不要 ```json 包裹，不要解释性文字。\n"
        f"6. JSON 必须使用双引号，示例：\n"
        f"{{\n"
        f'  "title": "震惊体或深度体标题",\n'
        f'  "author": "笔名",\n'
        f'  "digest": "120字摘要",\n'
        f'  "cover_prompt": "English prompt for cover image (16:9)",\n'
        f'  "sections": [\n'
        f'     {{ "content": "<p>第一部分文字...</p>", "image_prompt": "Optional English prompt for inline image (16:9) or null" }},\n'
        f'     {{ "content": "<h2>小标题</h2><p>第二部分文字...</p>", "image_prompt": null }}\n'
        f"  ]\n"
        f"}}"
    )

    try:
        gen_resp = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=structure_prompt,
            config={"response_mime_type": "application/json"},
        )
        article_data = _normalize_article_data(
            _parse_article_json(gen_resp.text),
            str(topic),
        )
    except Exception as e:
        logger.error(f"Generation error: {e}")
        yield f"❌ 创作失败: {e}"
        return

    # 3. 并行生成图片
    yield "🎨 正在并行绘制封面与插图..."

    # 收集所有需要生成的任务
    image_tasks = []

    # Cover
    if article_data.get("cover_prompt"):
        image_tasks.append(("cover", -1, article_data["cover_prompt"]))

    # Sections
    sections = article_data["sections"]
    for idx, sec in enumerate(sections):
        if sec.get("image_prompt"):
            image_tasks.append(("section", idx, sec["image_prompt"]))

    # 执行生成
    async def gen_img(task):
        t_type, t_idx, t_prompt = task
        try:
            # Add style modifiers
            full_prompt = f"{t_prompt}, vector illustration, flat style, tech vibe, high quality, 4k"
            res = await ctx.run_skill(
                "generate_image", {"prompt": full_prompt, "aspect_ratio": "16:9"}
            )
            if isinstance(res, dict) and res.get("files"):
                # Return first file content
                return (t_type, t_idx, list(res["files"].values())[0])
        except Exception as e:
            logger.warning(f"Image gen failed for {t_type}:{t_idx}: {e}")
        return (t_type, t_idx, None)

    # Launch all
    img_results = await asyncio.gather(*[gen_img(t) for t in image_tasks])

    # Organize images
    cover_bytes = None
    section_images = {}  # idx -> bytes

    generated_files = {}  # For returning to user

    for res_type, res_idx, res_bytes in img_results:
        if not res_bytes:
            continue

        fname = f"img_{res_type}_{res_idx}.png"
        generated_files[fname] = res_bytes

        if res_type == "cover":
            cover_bytes = res_bytes
        else:
            section_images[res_idx] = res_bytes

    # 4. 发布 或 组装本地预览
    final_output_md = (
        f"# {article_data['title']}\n*By {article_data['author']}*\n\n"
    )
    final_output_md += f"> {article_data['digest']}\n\n"

    if cover_bytes:
        final_output_md += "![Cover](img_cover_-1.png)\n\n"

    # HTML 组装 (用于公众号)
    full_html = ""
    publish_status = ""

    if publish:
        yield "📤 正在上传素材并同步至微信后台..."
        try:
            user_id = ctx.message.user.id
            account = await get_account(user_id, "wechat_official_account")
            if not account:
                publish_status = (
                    "\n⚠️ **发布中止**: 未配置公众号凭证 (wechat_official_account)。"
                )
            else:
                app_id = account.get("app_id") if isinstance(account, dict) else None
                app_secret = (
                    account.get("app_secret") if isinstance(account, dict) else None
                )
                if not app_id or not app_secret:
                    publish_status = (
                        "\n⚠️ **发布中止**: 公众号凭证缺少 app_id 或 app_secret。"
                    )
                    publisher = None
                else:
                    publisher = WeChatPublisher(app_id, app_secret)

                # Upload Cover
                thumb_media_id = None
                if publisher and cover_bytes:
                    thumb_media_id = await publisher.upload_cover_image(cover_bytes)
                elif not cover_bytes:
                    publish_status += "\n⚠️ 未生成封面图，发布可能受限。"

                # 拼接并替换 HTML 图片
                for idx, sec in enumerate(sections):
                    content = sec.get("content", "")
                    full_html += f"{content}"

                    if publisher and idx in section_images:
                        img_bytes = section_images[idx]
                        try:
                            img_url = await publisher.upload_article_image(img_bytes)
                            full_html += f'<p><img src="{img_url}"/></p>'
                        except Exception as e:
                            logger.error(f"Failed to upload inline img: {e}")

                if not publisher:
                    pass
                elif thumb_media_id:
                    # Fix digest limit
                    digest_text = article_data.get("digest", "")
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
                    publish_status = (
                        f"\n✅ **发布成功**: [草稿箱 MediaID: `{draft_id}`]"
                    )
                else:
                    publish_status += "\n❌ **发布中止**: 封面图失败。"

        except Exception as e:
            logger.error(f"Publish failed: {e}", exc_info=True)
            publish_status = f"\n❌ **发布失败**: {e}"
    else:
        # 本地预览模式
        for idx, sec in enumerate(sections):
            content = sec.get("content", "")
            final_output_md += f"{content}\n\n"
            if idx in section_images:
                final_output_md += f"![Image {idx}](img_section_{idx}.png)\n\n"

    # Final Result
    yield {
        "text": final_output_md + "\n---\n" + publish_status,
        "files": generated_files,
        "ui": {"actions": []},
    }
