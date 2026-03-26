import importlib
import logging
import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

_state_io = importlib.import_module("core.state_io")
init_db = _state_io.init_db
now_iso = _state_io.now_iso
read_json = _state_io.read_json
write_json = _state_io.write_json

_state_paths = importlib.import_module("core.state_paths")
system_path = _state_paths.system_path
user_path = _state_paths.user_path
SINGLE_USER_SCOPE = _state_paths.SINGLE_USER_SCOPE
DATA_DIR = importlib.import_module("core.config").DATA_DIR

logger = logging.getLogger(__name__)

_ENTRY_RE = re.compile(r"^###\s+(system|user|model)\s*\n```text\n(.*?)\n```", re.M | re.S)
_VISIBLE_CHAT_ROLES = {"user", "model"}
_SUPPORTED_CHAT_ROLES = _VISIBLE_CHAT_ROLES | {"system"}


def _safe_session_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return str(uuid.uuid4())
    safe = re.sub(r"[^a-zA-Z0-9_\-:.]+", "_", raw)
    return safe or str(uuid.uuid4())


def _safe_user_scope(user_id: int | str) -> str:
    raw = str(user_id or "").strip()
    if not raw:
        return SINGLE_USER_SCOPE
    safe = quote(raw, safe="._-:")
    return safe or SINGLE_USER_SCOPE


def _legacy_chat_root() -> Path:
    return user_path(SINGLE_USER_SCOPE, "chat")


def _scoped_chat_root(user_id: int | str) -> Path:
    scope = _safe_user_scope(user_id)
    root = (
        Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()
        / "user"
        / "chat_scoped"
        / scope
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _chat_root(user_id: int | str) -> Path:
    scope = _safe_user_scope(user_id)
    if scope == SINGLE_USER_SCOPE:
        root = _legacy_chat_root()
        root.mkdir(parents=True, exist_ok=True)
        return root
    return _scoped_chat_root(user_id)


def _session_path(user_id: int | str, day: str, session_id: str) -> Path:
    return (_chat_root(user_id) / day / f"{_safe_session_id(session_id)}.md").resolve()


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


def _normalize_chat_role(role: str) -> str:
    safe_role = str(role or "").strip().lower()
    if safe_role in _SUPPORTED_CHAT_ROLES:
        return safe_role
    return "user"


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
                "role": _normalize_chat_role(role),
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
    *,
    include_system: bool = False,
    preserve_system_prefixes: tuple[str, ...] = (),
    preserve_system_limit: int = 0,
) -> list[dict[str, Any]]:
    try:
        uid = str(user_id)
        path = await _resolve_session_file(uid, session_id)
        if not path or not path.exists():
            return []
        rows = _parse_entries(path.read_text(encoding="utf-8"))
        visible_rows = [
            item
            for item in rows
            if str(item.get("role") or "").strip().lower() != "system"
        ]
        tail = visible_rows[-max(1, int(limit)) :]
        selected_rows = list(tail)
        if include_system:
            system_rows = []
            for item in rows:
                if str(item.get("role") or "").strip().lower() != "system":
                    continue
                content = str(item.get("content") or "")
                if preserve_system_prefixes and not any(
                    content.startswith(prefix) for prefix in preserve_system_prefixes
                ):
                    continue
                system_rows.append(item)
            if preserve_system_limit > 0:
                system_rows = system_rows[-preserve_system_limit:]
            selected_rows = list(system_rows) + selected_rows
        return [
            {
                "role": _normalize_chat_role(str(item.get("role") or "user")),
                "parts": [{"text": str(item.get("content") or "")}],
            }
            for item in selected_rows
        ]
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        return []


async def get_session_entries(
    user_id: int | str,
    session_id: str,
) -> list[dict[str, str]]:
    try:
        uid = str(user_id)
        path = await _resolve_session_file(uid, session_id)
        if not path or not path.exists():
            return []
        rows = _parse_entries(path.read_text(encoding="utf-8"))
        return [
            {
                "role": _normalize_chat_role(str(item.get("role") or "user")),
                "content": str(item.get("content") or "").strip(),
            }
            for item in rows
            if str(item.get("content") or "").strip()
        ]
    except Exception as e:
        logger.error(f"Error reading session entries: {e}")
        return []


async def replace_session_entries(
    user_id: int | str,
    session_id: str,
    rows: list[dict[str, str]],
) -> bool:
    try:
        uid = str(user_id)
        sid = _safe_session_id(session_id)
        session_file = await _resolve_session_file(uid, sid)
        if session_file is None:
            session_file = _session_path(uid, date.today().isoformat(), sid)
        session_file.parent.mkdir(parents=True, exist_ok=True)
        day = _extract_day_from_path(session_file)
        normalized_rows = [
            {
                "role": _normalize_chat_role(str(item.get("role") or "user")),
                "content": str(item.get("content") or "").strip(),
            }
            for item in list(rows or [])
            if str(item.get("content") or "").strip()
        ]
        session_file.write_text(
            _render_session(day, sid, normalized_rows),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        logger.error(f"Error replacing session entries: {e}")
        return False


async def get_latest_session_id(user_id: int | str) -> str:
    try:
        uid = str(user_id)
        files = await _list_session_files(uid)
        if files:
            return _extract_session_from_path(files[0])
        return str(uuid.uuid4())
    except Exception as e:
        logger.error(f"Error getting latest session: {e}")
        return str(uuid.uuid4())


async def create_chat_session(
    user_id: int | str,
    session_id: str,
) -> dict[str, Any]:
    uid = str(user_id)
    sid = _safe_session_id(session_id)
    session_file = await _resolve_session_file(uid, sid)
    if session_file is None:
        session_file = _session_path(uid, date.today().isoformat(), sid)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    if not session_file.exists():
        session_file.write_text(
            _render_session(_extract_day_from_path(session_file), sid, []),
            encoding="utf-8",
        )
    stat = session_file.stat()
    return {
        "session_id": sid,
        "path": str(session_file),
        "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


async def list_chat_sessions(
    user_id: int | str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    try:
        uid = str(user_id)
        files = await _list_session_files(uid)
        sessions: list[dict[str, Any]] = []
        for path in files[: max(1, int(limit))]:
            rows = _parse_entries(path.read_text(encoding="utf-8"))
            visible_rows = [
                row
                for row in rows
                if str(row.get("role") or "").strip().lower() in _VISIBLE_CHAT_ROLES
            ]
            preview = str((visible_rows[-1] if visible_rows else {}).get("content") or "").strip()
            title = ""
            for item in visible_rows:
                if str(item.get("role") or "").strip().lower() == "user":
                    title = str(item.get("content") or "").strip()
                    break
            stat = path.stat()
            sessions.append(
                {
                    "session_id": _extract_session_from_path(path),
                    "title": (title[:48] if title else "新对话"),
                    "preview": preview[:120],
                    "message_count": len(visible_rows),
                    "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "path": str(path),
                }
            )
        return sessions
    except Exception as e:
        logger.error(f"Error listing chat sessions: {e}")
        return []


async def search_messages(
    user_id: int | str,
    keyword: str,
    *,
    limit: int = 20,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
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
                if str(row.get("role") or "").strip().lower() == "system":
                    continue
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
) -> list[dict[str, Any]]:
    try:
        uid = str(user_id)
        files = await _list_session_files(uid)
        output: list[dict[str, Any]] = []
        for path in files:
            day = _extract_day_from_path(path)
            sid = _extract_session_from_path(path)
            rows = _parse_entries(path.read_text(encoding="utf-8"))
            for row in reversed(rows):
                if str(row.get("role") or "").strip().lower() == "system":
                    continue
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
                if role == "system":
                    continue
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


def _allowed_path():
    return system_path("allowed_users.md")


async def _read_allowed() -> list[dict[str, str]]:
    payload = await read_json(_allowed_path(), [])
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "user_id": str(item.get("user_id") or "").strip(),
                "added_by": str(item.get("added_by") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "created_at": str(item.get("created_at") or now_iso()),
            }
        )
    return [item for item in rows if item["user_id"]]


async def add_allowed_user(
    user_id: int | str,
    added_by: int | str | None = None,
    description: str | None = None,
):
    uid = str(user_id).strip()
    if not uid:
        return
    rows = await _read_allowed()
    if any(item["user_id"] == uid for item in rows):
        return
    rows.append(
        {
            "user_id": uid,
            "added_by": str(added_by or "").strip(),
            "description": str(description or "").strip(),
            "created_at": now_iso(),
        }
    )
    await write_json(_allowed_path(), rows)


async def remove_allowed_user(user_id: int | str):
    uid = str(user_id).strip()
    rows = await _read_allowed()
    kept = [item for item in rows if item["user_id"] != uid]
    if len(kept) != len(rows):
        await write_json(_allowed_path(), kept)


async def get_allowed_users() -> list[dict[str, str]]:
    return await _read_allowed()


async def check_user_allowed_in_db(user_id: int | str) -> bool:
    uid = str(user_id).strip()
    rows = await _read_allowed()
    return any(item["user_id"] == uid for item in rows)
