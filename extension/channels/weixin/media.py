from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
WEIXIN_MEDIA_MAX_BYTES = 100 * 1024 * 1024

UPLOAD_MEDIA_TYPE_IMAGE = 1
UPLOAD_MEDIA_TYPE_VIDEO = 2
UPLOAD_MEDIA_TYPE_FILE = 3

MESSAGE_ITEM_TYPE_TEXT = 1
MESSAGE_ITEM_TYPE_IMAGE = 2
MESSAGE_ITEM_TYPE_FILE = 4
MESSAGE_ITEM_TYPE_VIDEO = 5

_EXTENSION_TO_MIME = {
    ".bmp": "image/bmp",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".gif": "image/gif",
    ".gz": "application/gzip",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".ogg": "audio/ogg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".tar": "application/x-tar",
    ".txt": "text/plain",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}
_MIME_TO_EXTENSION = {
    "application/gzip": ".gz",
    "application/msword": ".doc",
    "application/octet-stream": ".bin",
    "application/pdf": ".pdf",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/x-tar": ".tar",
    "application/zip": ".zip",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
}


@dataclass(frozen=True)
class UploadedWeixinMedia:
    filekey: str
    download_encrypted_query_param: str
    aes_key_hex: str
    plaintext_size: int
    ciphertext_size: int

    @property
    def aes_key_b64(self) -> str:
        """
        Match the official @tencent-weixin/openclaw-weixin implementation.

        Their sender base64-encodes the hex string itself rather than the raw
        16-byte key. Downstream decode paths explicitly support this encoding.
        """
        return base64.b64encode(self.aes_key_hex.encode("ascii")).decode("ascii")


def normalize_cdn_base_url(value: str) -> str:
    rendered = str(value or DEFAULT_CDN_BASE_URL).strip() or DEFAULT_CDN_BASE_URL
    return rendered.rstrip("/")


def guess_mime_type(filename: str) -> str:
    candidate = str(filename or "").strip()
    guessed, _ = mimetypes.guess_type(candidate)
    if guessed:
        return guessed.split(";", 1)[0].strip().lower()
    return _EXTENSION_TO_MIME.get(
        Path(candidate).suffix.lower(), "application/octet-stream"
    )


def extension_from_content_type_or_url(content_type: str | None, url: str) -> str:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized:
        guessed = _MIME_TO_EXTENSION.get(normalized)
        if guessed:
            return guessed

    parsed = urlparse(str(url or ""))
    suffix = Path(parsed.path).suffix.lower()
    if suffix in _EXTENSION_TO_MIME:
        return suffix
    return ".bin"


def default_suffix_for_mime(mime_type: str) -> str:
    normalized = str(mime_type or "").split(";", 1)[0].strip().lower()
    return _MIME_TO_EXTENSION.get(normalized, ".bin")


def classify_media_kind(mime_type: str) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    return "file"


def upload_media_type_for_kind(kind: str) -> int:
    normalized = str(kind or "").strip().lower()
    if normalized == "image":
        return UPLOAD_MEDIA_TYPE_IMAGE
    if normalized == "video":
        return UPLOAD_MEDIA_TYPE_VIDEO
    return UPLOAD_MEDIA_TYPE_FILE


def aes_ecb_padded_size(plaintext_size: int) -> int:
    safe_size = max(0, int(plaintext_size or 0))
    return ((safe_size // 16) + 1) * 16


def build_cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{normalize_cdn_base_url(cdn_base_url)}/upload"
        f"?encrypted_query_param={quote(str(upload_param or ''), safe='')}"
        f"&filekey={quote(str(filekey or ''), safe='')}"
    )


def build_cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    return (
        f"{normalize_cdn_base_url(cdn_base_url)}/download"
        f"?encrypted_query_param={quote(str(encrypted_query_param or ''), safe='')}"
    )


def parse_aes_key_base64(aes_key_base64: str) -> bytes:
    decoded = base64.b64decode(str(aes_key_base64 or "").strip())
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        ascii_text = decoded.decode("ascii", errors="strict")
        if len(ascii_text) == 32 and all(
            ch in "0123456789abcdefABCDEF" for ch in ascii_text
        ):
            return bytes.fromhex(ascii_text)
    raise ValueError("Unsupported Weixin aes_key encoding")


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(bytes(plaintext or b"")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(bytes(ciphertext or b"")) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def build_image_message_item(uploaded: UploadedWeixinMedia) -> dict[str, object]:
    return {
        "type": MESSAGE_ITEM_TYPE_IMAGE,
        "image_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": uploaded.aes_key_b64,
                "encrypt_type": 1,
            },
            "mid_size": uploaded.ciphertext_size,
        },
    }


def build_video_message_item(uploaded: UploadedWeixinMedia) -> dict[str, object]:
    return {
        "type": MESSAGE_ITEM_TYPE_VIDEO,
        "video_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": uploaded.aes_key_b64,
                "encrypt_type": 1,
            },
            "video_size": uploaded.ciphertext_size,
        },
    }


def build_file_message_item(
    uploaded: UploadedWeixinMedia, file_name: str
) -> dict[str, object]:
    safe_name = Path(str(file_name or "file.bin")).name or "file.bin"
    return {
        "type": MESSAGE_ITEM_TYPE_FILE,
        "file_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": uploaded.aes_key_b64,
                "encrypt_type": 1,
            },
            "file_name": safe_name,
            "len": str(uploaded.plaintext_size),
        },
    }
