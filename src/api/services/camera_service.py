from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.camera import Camera
from api.services.camera_crypto import decrypt_secret, encrypt_secret

_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")


def normalize_mediamtx_path(value: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return f"ikaros_cam_{uuid4().hex[:10]}"
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")
    if not normalized:
        normalized = f"ikaros_cam_{uuid4().hex[:10]}"
    if not _PATH_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=400,
            detail="MediaMTX path must contain only letters, numbers, '_' or '-'",
        )
    return normalized[:120]


def _require_text(value: str | None, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    return text


def camera_to_dict(camera: Camera, *, include_sensitive: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": camera.id,
        "name": camera.name,
        "enabled": camera.enabled,
        "mediamtx_path": camera.mediamtx_path,
        "rtsp_configured": bool(camera.rtsp_url_encrypted),
        "onvif_enabled": camera.onvif_enabled,
        "onvif_host": camera.onvif_host or "",
        "onvif_port": camera.onvif_port or 80,
        "onvif_username": camera.onvif_username or "",
        "onvif_configured": bool(camera.onvif_host and camera.onvif_username),
        "created_at": camera.created_at.isoformat() if camera.created_at else None,
        "updated_at": camera.updated_at.isoformat() if camera.updated_at else None,
    }
    if include_sensitive:
        data["rtsp_url"] = decrypt_secret(camera.rtsp_url_encrypted)
        data["onvif_password"] = decrypt_secret(camera.onvif_password_encrypted)
    return data


async def list_cameras(session: AsyncSession) -> list[Camera]:
    result = await session.execute(select(Camera).order_by(Camera.id.asc()))
    return list(result.scalars().all())


async def get_camera(session: AsyncSession, camera_id: int) -> Camera:
    camera = await session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


def get_camera_rtsp_url(camera: Camera) -> str:
    return decrypt_secret(camera.rtsp_url_encrypted)


def get_camera_onvif_password(camera: Camera) -> str:
    return decrypt_secret(camera.onvif_password_encrypted)


async def create_camera(session: AsyncSession, payload: Any) -> Camera:
    now = datetime.utcnow()
    camera = Camera(
        name=_require_text(getattr(payload, "name", None), "name"),
        enabled=bool(getattr(payload, "enabled", True)),
        mediamtx_path=normalize_mediamtx_path(getattr(payload, "mediamtx_path", None)),
        rtsp_url_encrypted=encrypt_secret(
            _require_text(getattr(payload, "rtsp_url", None), "rtsp_url")
        ),
        onvif_enabled=bool(getattr(payload, "onvif_enabled", True)),
        onvif_host=str(getattr(payload, "onvif_host", "") or "").strip() or None,
        onvif_port=int(getattr(payload, "onvif_port", None) or 80),
        onvif_username=str(getattr(payload, "onvif_username", "") or "").strip() or None,
        onvif_password_encrypted=encrypt_secret(getattr(payload, "onvif_password", None)),
        created_at=now,
        updated_at=now,
    )
    session.add(camera)
    try:
        await session.flush()
        await session.refresh(camera)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="MediaMTX path already exists") from exc
    return camera


async def update_camera(session: AsyncSession, camera: Camera, payload: Any) -> Camera:
    changed = False
    for field in ("name", "enabled", "onvif_enabled", "onvif_host", "onvif_port", "onvif_username"):
        value = getattr(payload, field, None)
        if value is None:
            continue
        if field in {"onvif_host", "onvif_username"}:
            value = str(value or "").strip() or None
        if field == "onvif_port":
            value = int(value or 80)
        setattr(camera, field, value)
        changed = True

    if getattr(payload, "mediamtx_path", None) is not None:
        camera.mediamtx_path = normalize_mediamtx_path(payload.mediamtx_path)
        changed = True
    if getattr(payload, "rtsp_url", None) is not None:
        camera.rtsp_url_encrypted = encrypt_secret(
            _require_text(payload.rtsp_url, "rtsp_url")
        )
        changed = True
    if getattr(payload, "onvif_password", None) is not None:
        camera.onvif_password_encrypted = encrypt_secret(payload.onvif_password)
        changed = True

    if changed:
        camera.updated_at = datetime.utcnow()
    try:
        await session.flush()
        await session.refresh(camera)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="MediaMTX path already exists") from exc
    return camera
