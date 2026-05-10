from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

import httpx
from fastapi import HTTPException, Request

from api.core.config import settings
from core.app_paths import app_home


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name) or default).strip())
    except Exception:
        return default


STREAM_TOKEN_TTL_SECONDS = max(30, _env_int("CAMERA_STREAM_TOKEN_TTL_SECONDS", 300))


def _mediamtx_env_file() -> Path:
    configured = str(os.getenv("MEDIAMTX_ENV_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (app_home() / "config" / "mediamtx" / "ikaros-mediamtx.env").resolve()


def _read_mediamtx_env() -> dict[str, str]:
    path = _mediamtx_env_file()
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text.removeprefix("export ").strip()
        key, value = text.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _setting(name: str, default: str = "") -> str:
    env_value = str(os.getenv(name) or "").strip()
    if env_value:
        return env_value
    return str(_read_mediamtx_env().get(name) or default).strip()


def _setting_int(name: str, default: int) -> int:
    try:
        return int(_setting(name, str(default)))
    except Exception:
        return default


def mediamtx_api_url() -> str:
    return _setting("MEDIAMTX_API_URL", "http://127.0.0.1:9997").rstrip("/")


MEDIAMTX_API_URL = mediamtx_api_url()


def _secret() -> bytes:
    material = f"ikaros-camera-stream-v1:{settings.auth.secret_key}".encode("utf-8")
    return hashlib.sha256(material).digest()


def _b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _b64decode(payload: str) -> bytes:
    padded = payload + ("=" * (-len(payload) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def create_stream_token(
    *,
    camera_id: int,
    path: str,
    user_id: int,
    ttl_seconds: int = STREAM_TOKEN_TTL_SECONDS,
) -> dict[str, Any]:
    expires_at = int(time.time()) + ttl_seconds
    payload = {
        "camera_id": int(camera_id),
        "path": str(path),
        "user_id": int(user_id),
        "exp": expires_at,
    }
    encoded_payload = _b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    )
    signature = hmac.new(_secret(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return {
        "token": f"{encoded_payload}.{_b64encode(signature)}",
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "ttl_seconds": ttl_seconds,
    }


def verify_stream_token(token: str, *, path: str | None = None) -> dict[str, Any]:
    raw = str(token or "").strip()
    if not raw or "." not in raw:
        raise HTTPException(status_code=401, detail="Missing stream token")
    encoded_payload, encoded_signature = raw.split(".", 1)
    expected = hmac.new(
        _secret(),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        provided = _b64decode(encoded_signature)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid stream token") from exc
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid stream token")
    try:
        payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid stream token") from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="Stream token expired")
    if path is not None and str(payload.get("path") or "") != str(path):
        raise HTTPException(status_code=403, detail="Stream token path mismatch")
    return payload


def token_from_mediamtx_auth(payload: dict[str, Any]) -> str:
    bearer = str(payload.get("token") or "").strip()
    if bearer:
        return bearer
    query = str(payload.get("query") or "").strip()
    values = parse_qs(query.lstrip("?"))
    return str((values.get("token") or [""])[0]).strip()


def _public_base_url(
    request: Request,
    env_name: str,
    port_env_name: str,
    default_port: int,
) -> str:
    configured = _setting(env_name)
    if configured:
        return configured.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = str(forwarded_proto or request.url.scheme or "http").split(",")[0].strip()
    host = request.url.hostname or "127.0.0.1"
    port = _setting_int(port_env_name, default_port)
    return f"{scheme}://{host}:{port}"


def build_stream_urls(request: Request, path: str, token: str) -> dict[str, str]:
    quoted_path = quote(path.strip("/"), safe="/")
    webrtc_base = _public_base_url(
        request,
        "MEDIAMTX_WEBRTC_BASE_URL",
        "MEDIAMTX_WEBRTC_PORT",
        8889,
    )
    hls_base = _public_base_url(
        request,
        "MEDIAMTX_HLS_BASE_URL",
        "MEDIAMTX_HLS_PORT",
        8888,
    )
    token_query = quote(token, safe="")
    return {
        "path": path,
        "webrtc_url": f"{webrtc_base}/{quoted_path}?token={token_query}&controls=true&muted=true&autoplay=true&playsInline=true",
        "webrtc_whep_url": f"{webrtc_base}/{quoted_path}/whep",
        "hls_page_url": f"{hls_base}/{quoted_path}?token={token_query}&controls=true&muted=true&autoplay=true&playsInline=true",
        "hls_url": f"{hls_base}/{quoted_path}/index.m3u8?token={token_query}",
    }


class MediaMTXClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = str(base_url).rstrip("/") if base_url else None

    def _base_url(self) -> str:
        return (self.base_url or mediamtx_api_url()).rstrip("/")

    async def _request(self, method: str, path: str, **kwargs):
        async with httpx.AsyncClient(timeout=5.0) as client:
            return await client.request(method, f"{self._base_url()}{path}", **kwargs)

    async def ensure_rtsp_path(self, *, path: str, rtsp_url: str) -> None:
        quoted = quote(path, safe="")
        body = {
            "source": rtsp_url,
            "sourceOnDemand": True,
            "rtspTransport": "tcp",
        }
        try:
            existing = await self._request("GET", f"/v3/config/paths/get/{quoted}")
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"MediaMTX Control API unavailable: {exc}",
            ) from exc

        if existing.status_code == 200:
            response = await self._request(
                "PATCH",
                f"/v3/config/paths/patch/{quoted}",
                json=body,
            )
        elif existing.status_code == 404:
            response = await self._request(
                "POST",
                f"/v3/config/paths/add/{quoted}",
                json=body,
            )
        else:
            raise HTTPException(
                status_code=503,
                detail=f"MediaMTX path lookup failed: HTTP {existing.status_code}",
            )

        if response.status_code // 100 != 2:
            raise HTTPException(
                status_code=503,
                detail=f"MediaMTX path update failed: HTTP {response.status_code}",
            )

    async def delete_path(self, path: str) -> None:
        quoted = quote(path, safe="")
        try:
            response = await self._request("DELETE", f"/v3/config/paths/delete/{quoted}")
        except httpx.HTTPError:
            return
        if response.status_code in {200, 404}:
            return


mediamtx_client = MediaMTXClient()
