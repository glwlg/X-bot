from __future__ import annotations

import logging
import mimetypes
from typing import Final

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_IMAGE_INPUT_BYTES: Final[int] = 8 * 1024 * 1024


class ImageInputDownloadError(RuntimeError):
    """Raised when a remote image cannot be safely resolved."""


def _normalize_mime_type(value: str) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _guess_mime_from_magic(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"

    stripped = data.lstrip()
    if stripped.startswith(b"<svg") or stripped.startswith(b"<?xml"):
        return "image/svg+xml"
    return ""


def guess_image_mime_type(
    data: bytes,
    *,
    declared_mime: str = "",
    source_name: str = "",
) -> str:
    normalized_declared = _normalize_mime_type(declared_mime)
    if normalized_declared.startswith("image/"):
        return normalized_declared

    guessed, _ = mimetypes.guess_type(str(source_name or ""))
    normalized_guessed = _normalize_mime_type(str(guessed or ""))
    if normalized_guessed.startswith("image/"):
        return normalized_guessed

    return _guess_mime_from_magic(bytes(data or b""))


async def fetch_image_from_url(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_INPUT_BYTES,
) -> tuple[bytes, str]:
    safe_url = str(url or "").strip()
    if not safe_url.lower().startswith(("http://", "https://")):
        raise ImageInputDownloadError("只支持 http/https 图片链接")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Accept": "image/*,*/*;q=0.8",
    }

    declared_mime = ""
    chunks: list[bytes] = []
    total_bytes = 0

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            async with client.stream("GET", safe_url, headers=headers) as response:
                response.raise_for_status()
                declared_mime = str(response.headers.get("Content-Type") or "")
                raw_length = str(response.headers.get("Content-Length") or "").strip()
                if raw_length:
                    try:
                        if int(raw_length) > int(max_bytes):
                            raise ImageInputDownloadError("图片超过大小限制")
                    except ValueError:
                        pass

                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > int(max_bytes):
                        raise ImageInputDownloadError("图片超过大小限制")
                    chunks.append(bytes(chunk))
    except ImageInputDownloadError:
        raise
    except httpx.HTTPStatusError as exc:
        raise ImageInputDownloadError(
            f"图片链接访问失败：HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ImageInputDownloadError("图片链接访问失败") from exc

    payload = b"".join(chunks)
    if not payload:
        raise ImageInputDownloadError("图片内容为空")

    mime_type = guess_image_mime_type(
        payload,
        declared_mime=declared_mime,
        source_name=safe_url,
    )
    if not mime_type.startswith("image/"):
        raise ImageInputDownloadError("链接内容不是图片")

    return payload, mime_type
