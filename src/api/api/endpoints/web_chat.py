from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from api.auth.models import User
from api.auth.router import require_viewer
from api.auth.schemas import TtsRequest, WebInboundEventCreate, WebSessionCreate
from core.channel_runtime_store import channel_runtime_store
from services.tts_service import synthesize_speech
from web_channel.store import (
    append_outbound_event,
    create_session_projection,
    enqueue_inbound_event,
    get_file_record,
    get_session_messages,
    get_session_projection,
    list_outbound_events,
    list_session_projections,
    register_artifact_file,
    register_upload_file,
    upsert_session_message,
)

router = APIRouter()


def _user_id(user: User) -> str:
    return str(user.id)


def _build_user_payload(user: User) -> dict[str, Any]:
    return {
        "user_id": _user_id(user),
        "username": user.username or user.email,
        "display_name": user.display_name or user.username or user.email,
    }


def _message_payload_for_user_event(
    session_id: str,
    payload: WebInboundEventCreate,
) -> dict[str, Any] | None:
    event_type = str(payload.type or "").strip()
    if event_type not in {"message_text", "message_file", "message_voice", "command"}:
        return None
    message_id = uuid.uuid4().hex
    if event_type in {"message_text", "command"}:
        content = str(payload.text or "").strip()
        message_type = "text"
    else:
        content = str(payload.caption or "").strip()
        message_type = "voice" if event_type == "message_voice" else "file"
    attachments = []
    if payload.file_id:
        attachments.append(
            {
                "id": payload.file_id,
                "file_id": payload.file_id,
                "kind": message_type,
                "name": str(payload.file_name or ""),
                "mime_type": str(payload.mime_type or "application/octet-stream"),
                "size": int(payload.file_size or 0),
            }
        )
    return {
        "id": message_id,
        "session_id": session_id,
        "role": "user",
        "content": content,
        "message_type": message_type,
        "attachments": attachments,
        "meta": dict(payload.metadata or {}),
    }


@router.get("/sessions")
async def list_sessions(
    user: User = Depends(require_viewer),
):
    return {
        "items": await list_session_projections(_user_id(user), limit=100),
    }


@router.post("/sessions")
async def create_session(
    payload: WebSessionCreate,
    user: User = Depends(require_viewer),
):
    session_id = uuid.uuid4().hex
    projection = await create_session_projection(
        user_id=_user_id(user),
        session_id=session_id,
        title=str(payload.title or "").strip(),
        preferences=dict(payload.preferences or {}),
    )
    channel_runtime_store.set_session_id(
        session_id=session_id,
        platform="web",
        platform_user_id=_user_id(user),
    )
    return projection["session"]


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str,
    user: User = Depends(require_viewer),
):
    projection = await get_session_projection(_user_id(user), session_id)
    channel_runtime_store.set_session_id(
        session_id=session_id,
        platform="web",
        platform_user_id=_user_id(user),
    )
    return {
        "session": projection.get("session") or {},
        "items": await get_session_messages(_user_id(user), session_id),
    }


@router.post("/sessions/{session_id}/events")
async def create_session_event(
    session_id: str,
    payload: WebInboundEventCreate,
    user: User = Depends(require_viewer),
):
    projection = await create_session_projection(
        user_id=_user_id(user),
        session_id=session_id,
    )
    message_projection = _message_payload_for_user_event(session_id, payload)
    if message_projection is not None:
        stored = await upsert_session_message(
            user_id=_user_id(user),
            session_id=session_id,
            message=message_projection,
        )
    else:
        stored = None
    event_payload = {
        **_build_user_payload(user),
        "session_id": session_id,
        "text": payload.text,
        "file_id": payload.file_id,
        "file_name": payload.file_name,
        "file_size": payload.file_size,
        "mime_type": payload.mime_type,
        "caption": payload.caption,
        "callback_data": payload.callback_data,
        "metadata": payload.metadata or {},
        "message_id": (stored or {}).get("id"),
    }
    queued = await enqueue_inbound_event(
        {
            "type": str(payload.type or "").strip(),
            "owner_user_id": _user_id(user),
            "session_id": session_id,
            "payload": event_payload,
        }
    )
    channel_runtime_store.set_session_id(
        session_id=session_id,
        platform="web",
        platform_user_id=_user_id(user),
    )
    return {
        "queued": queued,
        "message": stored,
        "session": projection.get("session") or {},
    }


@router.get("/sessions/{session_id}/stream")
async def session_stream(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    user: User = Depends(require_viewer),
):
    async def event_stream():
        last_seq = int(after or 0)
        while True:
            if await request.is_disconnected():
                return
            events = await list_outbound_events(
                owner_user_id=_user_id(user),
                session_id=session_id,
                after_seq=last_seq,
                limit=100,
            )
            if not events:
                yield ": keep-alive\n\n"
                await asyncio.sleep(1.0)
                continue
            for event in events:
                last_seq = max(last_seq, int(event.get("seq") or 0))
                payload = json.dumps(event.get("payload") or {}, ensure_ascii=False)
                yield (
                    f"id: {event.get('seq')}\n"
                    f"event: {event.get('type')}\n"
                    f"data: {payload}\n\n"
                )
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/uploads")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Query(default=""),
    user: User = Depends(require_viewer),
):
    suffix = Path(str(file.filename or "")).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        tmp_path = Path(handle.name)
        content = await file.read()
        handle.write(content)
    try:
        record = await register_upload_file(
            owner_user_id=_user_id(user),
            session_id=str(session_id or "").strip(),
            source_path=str(tmp_path),
            original_name=str(file.filename or "upload.bin"),
            mime_type=str(file.content_type or "application/octet-stream"),
            size=len(content),
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return record


@router.get("/files/{file_id}")
async def download_chat_file(
    file_id: str,
    user: User = Depends(require_viewer),
):
    record = await get_file_record(file_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="文件不存在")
    if str(record.get("owner_user_id") or "") != _user_id(user):
        raise HTTPException(status_code=403, detail="没有访问权限")
    path = Path(str(record.get("path") or "")).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path,
        media_type=str(record.get("mime_type") or "application/octet-stream"),
        filename=str(record.get("name") or path.name),
    )


@router.post("/sessions/{session_id}/tts")
async def create_tts_audio(
    session_id: str,
    payload: TtsRequest,
    user: User = Depends(require_viewer),
):
    messages = await get_session_messages(_user_id(user), session_id)
    target = next(
        (item for item in messages if str(item.get("id") or "") == str(payload.message_id or "")),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    content = str(target.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息内容为空")
    audio_bytes = await synthesize_speech(content, voice=payload.voice)
    if not audio_bytes:
        raise HTTPException(status_code=503, detail="TTS 当前不可用")
    artifact = await register_artifact_file(
        owner_user_id=_user_id(user),
        session_id=session_id,
        source=audio_bytes,
        file_name=f"{payload.message_id}.mp3",
        mime_type="audio/mpeg",
    )
    attachment = {
        "id": str(artifact.get("id") or ""),
        "file_id": str(artifact.get("id") or ""),
        "kind": "audio",
        "name": str(artifact.get("name") or ""),
        "mime_type": str(artifact.get("mime_type") or "audio/mpeg"),
        "size": int(artifact.get("size") or 0),
    }
    updated = await upsert_session_message(
        user_id=_user_id(user),
        session_id=session_id,
        message={
            "id": str(payload.message_id or ""),
            "role": str(target.get("role") or "assistant"),
            "content": content,
            "message_type": str(target.get("message_type") or "text"),
            "attachments": list(target.get("attachments") or []) + [attachment],
            "meta": {**dict(target.get("meta") or {}), "tts_generated": True},
        },
    )
    await append_outbound_event(
        owner_user_id=_user_id(user),
        session_id=session_id,
        event_type="audio_ready",
        payload={"message_id": payload.message_id, "attachment": attachment},
    )
    return {
        "message": updated,
        "attachment": attachment,
    }
