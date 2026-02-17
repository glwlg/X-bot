"""Chat history repository backed by per-session Markdown files.

Layout:
  data/users/<user_id>/chat/<YYYY-MM-DD>/<session_id>.md
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import user_path

logger = logging.getLogger(__name__)

_ENTRY_RE = re.compile(r"^###\s+(user|model)\s*\n```text\n(.*?)\n```", re.M | re.S)


def _safe_session_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return str(uuid.uuid4())
    safe = re.sub(r"[^a-zA-Z0-9_\-:.]+", "_", raw)
    return safe or str(uuid.uuid4())


def _chat_root(user_id: int | str) -> Path:
    return user_path(user_id, "chat")


def _session_path(user_id: int | str, day: str, session_id: str) -> Path:
    return user_path(user_id, "chat", day, f"{_safe_session_id(session_id)}.md")


def _entry_block(role: str, content: str) -> str:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return f"### {role}\n```text\n{text}\n```\n\n"


def _parse_entries(content: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    raw = str(content or "")
    for match in _ENTRY_RE.finditer(raw):
        role = str(match.group(1) or "user").strip().lower()
        body = str(match.group(2) or "").strip()
        if not body:
            continue
        rows.append({"role": role, "content": body})
    return rows


def _render_session(day: str, session_id: str, rows: list[dict[str, str]]) -> str:
    lines = [
        "# Chat Session",
        "",
        f"- date: {day}",
        f"- session: {session_id}",
        "",
        "## Dialogue",
        "",
    ]
    body = "".join(
        _entry_block(str(item.get("role") or "user"), str(item.get("content") or ""))
        for item in rows
    )
    return "\n".join(lines) + body


def _extract_day_from_path(path: Path) -> str:
    parent = path.parent.name
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parent):
        return parent
    return date.today().isoformat()


def _extract_session_from_path(path: Path) -> str:
    return str(path.stem or "").strip() or str(uuid.uuid4())


async def _list_session_files(user_id: int | str) -> list[Path]:
    root = _chat_root(user_id)
    if not root.exists():
        return []
    files = [p for p in root.glob("*/*.md") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


async def _resolve_session_file(user_id: int | str, session_id: str) -> Path | None:
    sid = _safe_session_id(session_id)
    root = _chat_root(user_id)
    if not root.exists():
        return None
    today_path = _session_path(user_id, date.today().isoformat(), sid)
    if today_path.exists():
        return today_path
    for p in root.glob(f"*/{sid}.md"):
        if p.is_file():
            return p
    return None


async def save_message(
    user_id: int | str, role: str, content: str, session_id: str
) -> bool:
    """Append one message into a session markdown file."""
    try:
        uid = str(user_id)
        sid = _safe_session_id(session_id)
        session_file = await _resolve_session_file(uid, sid)
        if session_file is None:
            session_file = _session_path(uid, date.today().isoformat(), sid)

        session_file.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            session_file.read_text(encoding="utf-8") if session_file.exists() else ""
        )
        day = _extract_day_from_path(session_file)
        rows = _parse_entries(existing)
        rows.append(
            {
                "role": str(role or "user").strip().lower() or "user",
                "content": str(content or "").strip(),
            }
        )
        session_file.write_text(_render_session(day, sid, rows), encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"Error saving message: {e}")
        return False


async def get_session_messages(
    user_id: int | str,
    session_id: str,
    limit: int = 20,
) -> List[Dict]:
    """Return current session history in Gemini format."""
    try:
        uid = str(user_id)
        path = await _resolve_session_file(uid, session_id)
        if not path or not path.exists():
            return []
        rows = _parse_entries(path.read_text(encoding="utf-8"))
        tail = rows[-max(1, int(limit)) :]
        return [
            {
                "role": str(item.get("role") or "user"),
                "parts": [{"text": str(item.get("content") or "")}],
            }
            for item in tail
        ]
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        return []


async def get_latest_session_id(user_id: int | str) -> str:
    """Get latest session id; create new one if none exists."""
    try:
        uid = str(user_id)
        files = await _list_session_files(uid)
        if files:
            return _extract_session_from_path(files[0])
        return str(uuid.uuid4())
    except Exception as e:
        logger.error(f"Error getting latest session: {e}")
        return str(uuid.uuid4())


async def search_messages(
    user_id: int | str,
    keyword: str,
    *,
    limit: int = 20,
    session_id: Optional[str] = None,
) -> List[Dict]:
    """Search messages by keyword across markdown sessions."""
    text = str(keyword or "").strip()
    if not text:
        return []
    needle = text.lower()
    try:
        uid = str(user_id)
        files = await _list_session_files(uid)
        if session_id:
            sid = _safe_session_id(session_id)
            files = [p for p in files if p.stem == sid]

        matched: list[dict[str, Any]] = []
        for path in files:
            day = _extract_day_from_path(path)
            sid = _extract_session_from_path(path)
            rows = _parse_entries(path.read_text(encoding="utf-8"))
            for row in reversed(rows):
                content = str(row.get("content") or "")
                if needle not in content.lower():
                    continue
                matched.append(
                    {
                        "role": str(row.get("role") or "user"),
                        "content": content,
                        "created_at": day,
                        "session_id": sid,
                    }
                )
                if len(matched) >= max(1, int(limit)):
                    return matched
        return matched
    except Exception as e:
        logger.error(f"Error searching messages: {e}")
        return []


async def get_recent_messages_for_user(
    *,
    user_id: int | str,
    limit: int = 50,
) -> List[Dict]:
    """Read recent messages across sessions (newest first)."""
    try:
        uid = str(user_id)
        files = await _list_session_files(uid)
        output: list[dict[str, Any]] = []
        for path in files:
            day = _extract_day_from_path(path)
            sid = _extract_session_from_path(path)
            rows = _parse_entries(path.read_text(encoding="utf-8"))
            for row in reversed(rows):
                output.append(
                    {
                        "role": str(row.get("role") or "user"),
                        "content": str(row.get("content") or ""),
                        "created_at": day,
                        "session_id": sid,
                    }
                )
                if len(output) >= max(1, int(limit)):
                    return output
        return output
    except Exception as e:
        logger.error(f"Error reading recent messages: {e}")
        return []


async def get_day_session_transcripts(
    *,
    user_id: int | str,
    day: date | None = None,
    max_sessions: int = 32,
    max_chars_per_session: int = 4000,
) -> list[dict[str, Any]]:
    """Return today's session transcripts for daily memory compaction."""
    try:
        uid = str(user_id)
        target_day = (day or date.today()).isoformat()
        day_dir = _chat_root(uid) / target_day
        if not day_dir.exists():
            return []

        files = [p for p in day_dir.glob("*.md") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        bundles: list[dict[str, Any]] = []
        for path in files[: max(1, int(max_sessions))]:
            sid = _extract_session_from_path(path)
            text = path.read_text(encoding="utf-8")
            rows = _parse_entries(text)
            if not rows:
                continue
            rendered_lines: list[str] = []
            for row in rows:
                role = str(row.get("role") or "user")
                content = str(row.get("content") or "").strip()
                if content:
                    rendered_lines.append(f"{role}: {content}")
            transcript = "\n".join(rendered_lines).strip()
            if len(transcript) > max_chars_per_session:
                transcript = transcript[-max_chars_per_session:]
            bundles.append(
                {
                    "session_id": sid,
                    "day": target_day,
                    "messages": rows,
                    "transcript": transcript,
                    "path": str(path),
                    "updated_at": datetime.fromtimestamp(
                        path.stat().st_mtime
                    ).isoformat(),
                }
            )
        return bundles
    except Exception as e:
        logger.error(f"Error loading day session transcripts: {e}")
        return []
