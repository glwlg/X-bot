from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.config import DATA_DIR
from core.state_store import create_chat_session, list_chat_sessions
from core.platform.models import MessageType
from shared.queue.jsonl_queue import FileLock, JsonlTable


WEB_CHANNEL_ROOT = (Path(DATA_DIR) / "web_channel").resolve()
WEB_CHANNEL_INBOX_DIR = (WEB_CHANNEL_ROOT / "inbox").resolve()
WEB_CHANNEL_OUTBOX_DIR = (WEB_CHANNEL_ROOT / "outbox").resolve()
WEB_CHANNEL_UPLOADS_DIR = (WEB_CHANNEL_ROOT / "uploads").resolve()
WEB_CHANNEL_ARTIFACTS_DIR = (WEB_CHANNEL_ROOT / "artifacts").resolve()
WEB_CHANNEL_FILES_DIR = (WEB_CHANNEL_ROOT / "files").resolve()
WEB_CHANNEL_SESSIONS_DIR = (WEB_CHANNEL_ROOT / "sessions").resolve()
WEB_CHANNEL_INBOX_TABLE = JsonlTable(str((WEB_CHANNEL_INBOX_DIR / "events.jsonl").resolve()))


for directory in (
    WEB_CHANNEL_ROOT,
    WEB_CHANNEL_INBOX_DIR,
    WEB_CHANNEL_OUTBOX_DIR,
    WEB_CHANNEL_UPLOADS_DIR,
    WEB_CHANNEL_ARTIFACTS_DIR,
    WEB_CHANNEL_FILES_DIR,
    WEB_CHANNEL_SESSIONS_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any) -> str:
    raw = _safe_text(value)
    if not raw:
        return ""
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)


def _file_meta_path(file_id: str) -> Path:
    safe_id = _slug(file_id) or uuid.uuid4().hex
    return (WEB_CHANNEL_FILES_DIR / f"{safe_id}.json").resolve()


def _outbox_table(user_id: str) -> JsonlTable:
    safe_user_id = _slug(user_id) or "__anonymous__"
    return JsonlTable(str((WEB_CHANNEL_OUTBOX_DIR / f"{safe_user_id}.jsonl").resolve()))


def _session_path(user_id: str, session_id: str) -> Path:
    safe_user_id = _slug(user_id) or "__anonymous__"
    safe_session_id = _slug(session_id) or uuid.uuid4().hex
    return (WEB_CHANNEL_SESSIONS_DIR / safe_user_id / f"{safe_session_id}.json").resolve()


def _session_default(session_id: str, *, title: str = "", preferences: dict[str, Any] | None = None) -> dict[str, Any]:
    current_time = now_iso()
    safe_title = _safe_text(title) or "新对话"
    return {
        "version": 1,
        "session": {
            "id": _safe_text(session_id),
            "title": safe_title,
            "preview": "",
            "message_count": 0,
            "created_at": current_time,
            "updated_at": current_time,
            "last_message_at": "",
            "preferences": dict(preferences or {}),
        },
        "messages": [],
    }


def _preview_for_message(message: dict[str, Any]) -> str:
    content = _safe_text(message.get("content"))
    if content:
        return content[:120]
    attachments = message.get("attachments")
    if isinstance(attachments, list) and attachments:
        first = attachments[0]
        if isinstance(first, dict):
            name = _safe_text(first.get("name")) or _safe_text(first.get("mime_type"))
            if name:
                return f"[附件] {name}"
    return ""


async def _read_session_payload(user_id: str, session_id: str) -> dict[str, Any]:
    path = _session_path(user_id, session_id)
    lock = path.with_suffix(path.suffix + ".lock")
    async with FileLock(lock):
        if not path.exists():
            return _session_default(session_id)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return _session_default(session_id)
        if not isinstance(loaded, dict):
            return _session_default(session_id)
        payload = _session_default(session_id)
        payload.update(loaded)
        session_meta = payload.get("session")
        payload["session"] = (
            {**payload["session"], **session_meta}
            if isinstance(session_meta, dict)
            else payload["session"]
        )
        messages = payload.get("messages")
        payload["messages"] = list(messages) if isinstance(messages, list) else []
        return payload


async def _write_session_payload(user_id: str, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _session_path(user_id, session_id)
    lock = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    async with FileLock(lock):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return payload


def infer_message_type(
    *,
    mime_type: str | None = None,
    file_name: str | None = None,
    force_voice: bool = False,
) -> MessageType:
    if force_voice:
        return MessageType.VOICE
    mime = _safe_text(mime_type).lower()
    guessed = mime
    if not guessed and file_name:
        guessed = _safe_text(mimetypes.guess_type(file_name)[0]).lower()
    if guessed.startswith("image/"):
        return MessageType.IMAGE
    if guessed.startswith("video/"):
        return MessageType.VIDEO
    if guessed.startswith("audio/"):
        if "ogg" in guessed or "opus" in guessed or "webm" in guessed:
            return MessageType.VOICE
        return MessageType.AUDIO
    return MessageType.DOCUMENT


async def register_upload_file(
    *,
    owner_user_id: str,
    source_path: str,
    original_name: str,
    mime_type: str,
    size: int,
    session_id: str = "",
) -> dict[str, Any]:
    file_id = uuid.uuid4().hex
    suffix = Path(original_name).suffix or mimetypes.guess_extension(mime_type or "") or ""
    target_path = (WEB_CHANNEL_UPLOADS_DIR / f"{file_id}{suffix}").resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_path)
    payload = {
        "id": file_id,
        "storage": "upload",
        "path": str(target_path),
        "owner_user_id": _safe_text(owner_user_id),
        "session_id": _safe_text(session_id),
        "name": _safe_text(original_name) or target_path.name,
        "mime_type": _safe_text(mime_type) or "application/octet-stream",
        "size": int(size or 0),
        "created_at": now_iso(),
    }
    _file_meta_path(file_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


async def register_artifact_file(
    *,
    owner_user_id: str,
    source: str | bytes,
    file_name: str,
    mime_type: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    file_id = uuid.uuid4().hex
    suffix = Path(file_name).suffix or mimetypes.guess_extension(mime_type or "") or ""
    target_path = (WEB_CHANNEL_ARTIFACTS_DIR / f"{file_id}{suffix}").resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, bytes):
        target_path.write_bytes(source)
    else:
        shutil.copyfile(str(source), target_path)
    resolved_mime = _safe_text(mime_type) or _safe_text(mimetypes.guess_type(file_name)[0]) or "application/octet-stream"
    payload = {
        "id": file_id,
        "storage": "artifact",
        "path": str(target_path),
        "owner_user_id": _safe_text(owner_user_id),
        "session_id": _safe_text(session_id),
        "name": _safe_text(file_name) or target_path.name,
        "mime_type": resolved_mime,
        "size": int(target_path.stat().st_size),
        "created_at": now_iso(),
    }
    _file_meta_path(file_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


async def get_file_record(file_id: str) -> dict[str, Any] | None:
    path = _file_meta_path(file_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


async def load_file_bytes(file_id: str) -> bytes:
    record = await get_file_record(file_id)
    if not isinstance(record, dict):
        raise FileNotFoundError(file_id)
    path = Path(str(record.get("path") or "")).resolve()
    if not path.exists():
        raise FileNotFoundError(file_id)
    return path.read_bytes()


async def enqueue_inbound_event(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "id": uuid.uuid4().hex,
        "status": "pending",
        "created_at": now_iso(),
        "claimed_at": "",
        "processed_at": "",
        **dict(payload or {}),
    }
    await WEB_CHANNEL_INBOX_TABLE.append(normalized)
    return normalized


async def claim_inbound_events(*, limit: int = 20) -> list[dict[str, Any]]:
    claimed: list[dict[str, Any]] = []
    claim_time = now_iso()
    async with WEB_CHANNEL_INBOX_TABLE._inproc_lock:
        async with FileLock(WEB_CHANNEL_INBOX_TABLE.lock_path):
            rows = WEB_CHANNEL_INBOX_TABLE._read_all_unlocked()
            changed = False
            for row in rows:
                if len(claimed) >= max(1, int(limit)):
                    break
                if _safe_text(row.get("status")).lower() != "pending":
                    continue
                row["status"] = "claimed"
                row["claimed_at"] = claim_time
                claimed.append(dict(row))
                changed = True
            if changed:
                WEB_CHANNEL_INBOX_TABLE._write_all_unlocked(rows)
    return claimed


async def ack_inbound_event(event_id: str, *, status: str = "done", error: str = "") -> None:
    async with WEB_CHANNEL_INBOX_TABLE._inproc_lock:
        async with FileLock(WEB_CHANNEL_INBOX_TABLE.lock_path):
            rows = WEB_CHANNEL_INBOX_TABLE._read_all_unlocked()
            changed = False
            for row in rows:
                if _safe_text(row.get("id")) != _safe_text(event_id):
                    continue
                row["status"] = _safe_text(status) or "done"
                row["processed_at"] = now_iso()
                row["error"] = _safe_text(error)
                changed = True
                break
            if changed:
                WEB_CHANNEL_INBOX_TABLE._write_all_unlocked(rows)


async def fail_inbound_event(event_id: str, error: str) -> None:
    await ack_inbound_event(event_id, status="failed", error=error)


async def append_outbound_event(
    *,
    owner_user_id: str,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    table = _outbox_table(owner_user_id)
    async with table._inproc_lock:
        async with FileLock(table.lock_path):
            existing = table._read_all_unlocked()
            seq = len(existing) + 1
            event = {
                "seq": seq,
                "id": uuid.uuid4().hex,
                "session_id": _safe_text(session_id),
                "type": _safe_text(event_type),
                "created_at": now_iso(),
                "payload": dict(payload or {}),
            }
            existing.append(event)
            table._write_all_unlocked(existing)
    return event


async def list_outbound_events(
    *,
    owner_user_id: str,
    after_seq: int = 0,
    session_id: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    table = _outbox_table(owner_user_id)
    rows = await table.read_all()
    target_session_id = _safe_text(session_id)
    output: list[dict[str, Any]] = []
    for row in rows:
        try:
            seq = int(row.get("seq") or 0)
        except Exception:
            seq = 0
        if seq <= int(after_seq or 0):
            continue
        if target_session_id and _safe_text(row.get("session_id")) not in {"", target_session_id}:
            continue
        output.append(dict(row))
        if len(output) >= max(1, int(limit)):
            break
    return output


async def ensure_session_projection(
    *,
    user_id: str,
    session_id: str,
    title: str = "",
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_user_id = _safe_text(user_id)
    safe_session_id = _safe_text(session_id)
    path = _session_path(safe_user_id, safe_session_id)
    lock = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    async with FileLock(lock):
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = _session_default(safe_session_id)
        else:
            payload = _session_default(safe_session_id)
        if not isinstance(payload, dict):
            payload = _session_default(safe_session_id)
        payload = _session_default(safe_session_id) | payload
        session = payload.get("session")
        if not isinstance(session, dict):
            session = _session_default(safe_session_id)["session"]
            payload["session"] = session
        payload["messages"] = list(payload.get("messages") or [])
        changed = False
        if title and _safe_text(session.get("title")) in {"", "新对话"}:
            session["title"] = _safe_text(title)
            changed = True
        if preferences:
            current_preferences = session.get("preferences")
            if not isinstance(current_preferences, dict):
                current_preferences = {}
            session["preferences"] = {**current_preferences, **dict(preferences)}
            changed = True
        if changed or not path.exists():
            session["updated_at"] = now_iso()
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    await create_chat_session(safe_user_id, safe_session_id)
    return await _read_session_payload(safe_user_id, safe_session_id)


async def create_session_projection(
    *,
    user_id: str,
    session_id: str,
    title: str = "",
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await ensure_session_projection(
        user_id=user_id,
        session_id=session_id,
        title=title,
        preferences=preferences,
    )


async def upsert_session_message(
    *,
    user_id: str,
    session_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    safe_user_id = _safe_text(user_id)
    safe_session_id = _safe_text(session_id)
    await ensure_session_projection(user_id=safe_user_id, session_id=safe_session_id)
    path = _session_path(safe_user_id, safe_session_id)
    lock = path.with_suffix(path.suffix + ".lock")
    async with FileLock(lock):
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = _session_default(safe_session_id)
        else:
            payload = _session_default(safe_session_id)
        if not isinstance(payload, dict):
            payload = _session_default(safe_session_id)
        session = payload.get("session")
        if not isinstance(session, dict):
            session = _session_default(safe_session_id)["session"]
            payload["session"] = session
        rows = list(payload.get("messages") or [])
        payload["messages"] = rows
        safe_message_id = _safe_text(message.get("id")) or uuid.uuid4().hex
        existing = None
        for row in rows:
            if _safe_text(row.get("id")) == safe_message_id:
                existing = row
                break
        current_time = now_iso()
        normalized = {
            "id": safe_message_id,
            "session_id": safe_session_id,
            "role": _safe_text(message.get("role")) or "assistant",
            "content": str(message.get("content") or ""),
            "status": _safe_text(message.get("status")) or "completed",
            "message_type": _safe_text(message.get("message_type")) or "text",
            "attachments": list(message.get("attachments") or []),
            "actions": list(message.get("actions") or []),
            "meta": dict(message.get("meta") or {}),
            "created_at": _safe_text(message.get("created_at")) or current_time,
            "updated_at": current_time,
        }
        if existing is not None:
            existing.update(normalized)
            target = existing
        else:
            rows.append(normalized)
            target = normalized
        if target["role"] == "user" and _safe_text(session.get("title")) in {"", "新对话"}:
            preview_title = _preview_for_message(target)
            if preview_title:
                session["title"] = preview_title[:48]
        session["preview"] = _preview_for_message(target)
        session["message_count"] = len(rows)
        session["updated_at"] = current_time
        session["last_message_at"] = target["updated_at"]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return dict(target)


async def get_session_projection(user_id: str, session_id: str) -> dict[str, Any]:
    return await ensure_session_projection(user_id=user_id, session_id=session_id)


async def get_session_messages(user_id: str, session_id: str) -> list[dict[str, Any]]:
    payload = await ensure_session_projection(user_id=user_id, session_id=session_id)
    return list(payload.get("messages") or [])


async def list_session_projections(user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    safe_user_id = _slug(user_id) or "__anonymous__"
    root = (WEB_CHANNEL_SESSIONS_DIR / safe_user_id).resolve()
    rows: list[dict[str, Any]] = []
    if root.exists():
        for path in root.glob("*.json"):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(loaded, dict):
                continue
            session_meta = loaded.get("session")
            if not isinstance(session_meta, dict):
                continue
            rows.append(dict(session_meta))
    if not rows:
        fallback_sessions = await list_chat_sessions(user_id, limit=limit)
        for item in fallback_sessions:
            rows.append(
                {
                    "id": _safe_text(item.get("session_id")),
                    "title": _safe_text(item.get("title")) or "历史会话",
                    "preview": _safe_text(item.get("preview")),
                    "message_count": int(item.get("message_count") or 0),
                    "created_at": _safe_text(item.get("created_at")),
                    "updated_at": _safe_text(item.get("updated_at")),
                    "last_message_at": _safe_text(item.get("updated_at")),
                    "preferences": {},
                }
            )
    rows.sort(
        key=lambda item: (
            _safe_text(item.get("updated_at")),
            _safe_text(item.get("last_message_at")),
            _safe_text(item.get("id")),
        ),
        reverse=True,
    )
    return rows[: max(1, int(limit))]
