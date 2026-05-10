from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from api.core.config import settings


def _fernet() -> Fernet:
    material = f"ikaros-camera-v1:{settings.auth.secret_key}".encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def encrypt_secret(value: str | None) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return _fernet().decrypt(raw.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("stored camera secret cannot be decrypted") from exc
