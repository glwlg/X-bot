from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import User
from api.auth.router import require_admin
from api.core.database import get_async_session
from api.services.camera_service import (
    camera_to_dict,
    create_camera,
    get_camera,
    get_camera_onvif_password,
    get_camera_rtsp_url,
    list_cameras,
    update_camera,
)
from api.services.mediamtx_service import (
    build_stream_urls,
    create_stream_token,
    mediamtx_client,
    token_from_mediamtx_auth,
    verify_stream_token,
)
from api.services.onvif_ptz import send_ptz_command

router = APIRouter()


class CameraCreate(BaseModel):
    name: str
    rtsp_url: str
    enabled: bool = True
    mediamtx_path: str | None = None
    onvif_enabled: bool = True
    onvif_host: str | None = None
    onvif_port: int | None = 80
    onvif_username: str | None = None
    onvif_password: str | None = None


class CameraUpdate(BaseModel):
    name: str | None = None
    rtsp_url: str | None = None
    enabled: bool | None = None
    mediamtx_path: str | None = None
    onvif_enabled: bool | None = None
    onvif_host: str | None = None
    onvif_port: int | None = None
    onvif_username: str | None = None
    onvif_password: str | None = None


class PTZRequest(BaseModel):
    action: Literal[
        "up",
        "down",
        "left",
        "right",
        "up_left",
        "up_right",
        "down_left",
        "down_right",
        "zoom_in",
        "zoom_out",
        "stop",
    ]
    speed: float = Field(default=0.4, ge=0.05, le=1.0)


class MediaMTXAuthRequest(BaseModel):
    user: str | None = None
    password: str | None = None
    token: str | None = None
    ip: str | None = None
    action: str | None = None
    path: str | None = None
    protocol: str | None = None
    id: str | None = None
    query: str | None = None


@router.get("")
async def get_cameras(
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    return [
        camera_to_dict(camera, include_sensitive=True)
        for camera in await list_cameras(session)
    ]


@router.post("")
async def post_camera(
    payload: CameraCreate,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    camera = await create_camera(session, payload)
    return camera_to_dict(camera, include_sensitive=True)


@router.put("/{camera_id}")
async def put_camera(
    camera_id: int,
    payload: CameraUpdate,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    camera = await get_camera(session, camera_id)
    previous_path = camera.mediamtx_path
    updated = await update_camera(session, camera, payload)
    if previous_path != updated.mediamtx_path:
        await mediamtx_client.delete_path(previous_path)
    return camera_to_dict(updated, include_sensitive=True)


@router.delete("/{camera_id}")
async def delete_camera(
    camera_id: int,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    camera = await get_camera(session, camera_id)
    path = camera.mediamtx_path
    await session.delete(camera)
    await mediamtx_client.delete_path(path)
    return {"success": True}


@router.post("/{camera_id}/stream-token")
async def create_camera_stream_token(
    camera_id: int,
    request: Request,
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    camera = await get_camera(session, camera_id)
    if not camera.enabled:
        raise HTTPException(status_code=400, detail="Camera is disabled")
    await mediamtx_client.ensure_rtsp_path(
        path=camera.mediamtx_path,
        rtsp_url=get_camera_rtsp_url(camera),
    )
    token_payload = create_stream_token(
        camera_id=camera.id,
        path=camera.mediamtx_path,
        user_id=admin_user.id,
    )
    return {
        **token_payload,
        **build_stream_urls(request, camera.mediamtx_path, token_payload["token"]),
    }


@router.post("/{camera_id}/test")
async def test_camera(
    camera_id: int,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    camera = await get_camera(session, camera_id)
    result: dict[str, Any] = {
        "mediamtx": {"ok": False, "detail": ""},
        "onvif": {"ok": False, "detail": "ONVIF is disabled or incomplete"},
    }
    try:
        await mediamtx_client.ensure_rtsp_path(
            path=camera.mediamtx_path,
            rtsp_url=get_camera_rtsp_url(camera),
        )
        result["mediamtx"] = {"ok": True, "detail": "MediaMTX path is ready"}
    except Exception as exc:
        result["mediamtx"] = {"ok": False, "detail": str(exc)}

    if camera.onvif_enabled and camera.onvif_host:
        try:
            await send_ptz_command(
                host=camera.onvif_host,
                port=camera.onvif_port or 80,
                username=camera.onvif_username or "",
                password=get_camera_onvif_password(camera),
                action="stop",
                speed=0.4,
            )
            result["onvif"] = {"ok": True, "detail": "ONVIF PTZ accepted Stop"}
        except Exception as exc:
            result["onvif"] = {"ok": False, "detail": str(exc)}
    return result


@router.post("/{camera_id}/ptz")
async def camera_ptz(
    camera_id: int,
    payload: PTZRequest,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    camera = await get_camera(session, camera_id)
    if not camera.onvif_enabled:
        raise HTTPException(status_code=400, detail="ONVIF PTZ is disabled")
    if not camera.onvif_host:
        raise HTTPException(status_code=400, detail="ONVIF host is not configured")
    try:
        await send_ptz_command(
            host=camera.onvif_host,
            port=camera.onvif_port or 80,
            username=camera.onvif_username or "",
            password=get_camera_onvif_password(camera),
            action=payload.action,
            speed=payload.speed,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True}


@router.post("/mediamtx/auth")
async def mediamtx_auth(payload: MediaMTXAuthRequest):
    data = payload.model_dump()
    action = str(data.get("action") or "").strip()
    if action in {"api", "metrics", "pprof"}:
        return {"success": True}
    if action not in {"read", "playback"}:
        raise HTTPException(status_code=403, detail="Unsupported MediaMTX action")

    path = str(data.get("path") or "").strip()
    token = token_from_mediamtx_auth(data)
    verify_stream_token(token, path=path)
    return {"success": True}
