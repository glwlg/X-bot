"""WeChat Official Account publisher."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Publish helpers
# ---------------------------------------------------------------------------

async def publish_to_wechat(
    *,
    publisher: WeChatPublisher,
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


def format_wechat_publish_preflight_error(exc: Exception) -> str:
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


async def prepare_wechat_publisher(
    account: dict[str, Any] | None,
) -> tuple[WeChatPublisher | None, str]:
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
        return None, format_wechat_publish_preflight_error(exc)
    return publisher, ""
