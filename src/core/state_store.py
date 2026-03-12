import importlib
import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from core.subscription_types import (
    default_title,
    normalize_platform,
    normalize_provider,
)

_state_io = importlib.import_module("core.state_io")
init_db = _state_io.init_db
next_id = _state_io.next_id
now_iso = _state_io.now_iso
read_json = _state_io.read_json
write_json = _state_io.write_json

_state_paths = importlib.import_module("core.state_paths")
all_user_ids = _state_paths.all_user_ids
system_path = _state_paths.system_path
user_path = _state_paths.user_path

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
) -> list[dict[str, Any]]:
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


def _accounts_path(user_id: int | str):
    return user_path(user_id, "accounts.md")


async def _read_accounts(user_id: int | str) -> dict[str, dict[str, Any]]:
    payload = await read_json(_accounts_path(user_id), {})
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for service, item in payload.items():
        if not isinstance(item, dict):
            continue
        result[str(service)] = {
            "data": dict(item.get("data") or {}),
            "updated_at": str(item.get("updated_at") or now_iso()),
        }
    return result


async def add_account(user_id: int | str, service: str, data: dict[str, Any]) -> bool:
    try:
        service_name = str(service or "").strip()
        if not service_name:
            return False
        payload = await _read_accounts(user_id)
        payload[service_name] = {
            "data": dict(data or {}),
            "updated_at": now_iso(),
        }
        await write_json(_accounts_path(user_id), payload)
        return True
    except Exception as e:
        logger.error(f"Error adding account: {e}")
        return False


async def get_account(user_id: int | str, service: str) -> dict[str, Any] | None:
    try:
        payload = await _read_accounts(user_id)
        item = payload.get(str(service or "").strip())
        if not isinstance(item, dict):
            return None
        data = item.get("data")
        return dict(data or {})
    except Exception as e:
        logger.error(f"Error getting account: {e}")
        return None


async def list_accounts(user_id: int | str) -> list[str]:
    try:
        payload = await _read_accounts(user_id)
        return sorted(payload.keys())
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        return []


async def delete_account(user_id: int | str, service: str) -> bool:
    try:
        payload = await _read_accounts(user_id)
        key = str(service or "").strip()
        if key not in payload:
            return False
        payload.pop(key, None)
        await write_json(_accounts_path(user_id), payload)
        return True
    except Exception as e:
        logger.error(f"Error deleting account: {e}")
        return False


def _cache_path():
    return system_path("video_cache.md")


async def save_video_cache(file_id: str, file_path: str):
    payload = await read_json(_cache_path(), {})
    if not isinstance(payload, dict):
        payload = {}
    fid = str(file_id or "").strip()
    if not fid:
        return
    payload[fid] = {
        "file_path": str(file_path or "").strip(),
        "created_at": now_iso(),
    }
    await write_json(_cache_path(), payload)


async def get_video_cache(file_id: str) -> str | None:
    payload = await read_json(_cache_path(), {})
    if not isinstance(payload, dict):
        return None
    item = payload.get(str(file_id or "").strip())
    if isinstance(item, dict):
        return str(item.get("file_path") or "") or None
    if isinstance(item, str):
        return item or None
    return None


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


def _subs_path() -> Path:
    return user_path("user", "rss", "subscriptions.md")


def _normalize_subscription(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    try:
        sub_id = int(raw.get("id") or 0)
    except Exception:
        return None
    if sub_id <= 0:
        return None

    user_id = str(raw.get("user_id") or "").strip()
    if not user_id:
        return None

    feed_url = str(raw.get("feed_url") or "").strip()
    if not feed_url:
        return None

    try:
        provider = normalize_provider(raw.get("provider"), feed_url=feed_url)
    except ValueError:
        return None

    title = str(raw.get("title") or "").strip()
    if not title:
        title = default_title(feed_url=feed_url)

    return {
        "id": sub_id,
        "user_id": user_id,
        "provider": provider,
        "title": title,
        "platform": normalize_platform(raw.get("platform")),
        "feed_url": feed_url,
        "last_etag": str(raw.get("last_etag") or "").strip(),
        "last_modified": str(raw.get("last_modified") or "").strip(),
        "last_entry_hash": str(raw.get("last_entry_hash") or "").strip(),
    }


def _serialize_subscription(row: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_subscription(row)
    if normalized is None:
        raise ValueError("invalid subscription row")
    return normalized


async def _read_subscription_rows() -> list[dict[str, Any]]:
    data = await read_json(_subs_path(), [])
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in data:
        normalized = _normalize_subscription(item)
        if normalized is not None:
            rows.append(normalized)
    return rows


async def _write_subscription_rows(rows: list[dict[str, Any]]) -> None:
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(_serialize_subscription(row))
    payload.sort(key=lambda item: (str(item.get("user_id") or ""), int(item["id"])))
    await write_json(_subs_path(), payload)


def _find_subscription_index(
    rows: list[dict[str, Any]], user_id: int | str, sub_id: int
) -> int:
    uid = str(user_id or "").strip()
    for index, row in enumerate(rows):
        if str(row.get("user_id") or "") != uid:
            continue
        if int(row.get("id") or 0) == int(sub_id):
            return index
    return -1


def _assert_unique_subscription(
    rows: list[dict[str, Any]],
    candidate: dict[str, Any],
    *,
    ignore_id: int | None = None,
) -> None:
    for row in rows:
        if ignore_id is not None and int(row.get("id") or 0) == int(ignore_id):
            continue
        if str(row.get("user_id") or "") != str(candidate.get("user_id") or ""):
            continue
        if str(row.get("feed_url") or "").strip() == str(candidate.get("feed_url") or "").strip():
            raise ValueError("feed subscription already exists")


def _validate_subscription_payload(
    user_id: int | str,
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    subscription_id: int | None = None,
) -> dict[str, Any]:
    source = dict(existing or {})
    source.update(dict(payload or {}))
    platform = normalize_platform(source.get("platform"))
    feed_url = str(source.get("feed_url") or "").strip()
    if not feed_url:
        raise ValueError("feed_url is required for RSS subscriptions")
    for removed_field in ("kind", "query", "scope"):
        if str(source.get(removed_field) or "").strip():
            raise ValueError("关键词监控已下线，仅支持 RSS 订阅")

    provider = normalize_provider(source.get("provider"), feed_url=feed_url)
    title = str(source.get("title") or "").strip()
    if not title:
        title = default_title(feed_url=feed_url)

    return {
        "id": int(subscription_id or source.get("id") or 0),
        "user_id": str(user_id or "").strip(),
        "provider": provider,
        "title": title,
        "platform": platform,
        "feed_url": feed_url,
        "last_etag": str(source.get("last_etag") or "").strip(),
        "last_modified": str(source.get("last_modified") or "").strip(),
        "last_entry_hash": str(source.get("last_entry_hash") or "").strip(),
    }


async def create_subscription(user_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = await _read_subscription_rows()
    sub_id = await next_id("subscriptions", start=1)
    record = _validate_subscription_payload(user_id, payload, subscription_id=sub_id)
    _assert_unique_subscription(rows, record)
    rows.append(record)
    await _write_subscription_rows(rows)
    return record


async def list_subscriptions(user_id: int | str) -> list[dict[str, Any]]:
    uid = str(user_id or "").strip()
    rows = await _read_subscription_rows()
    return [row for row in rows if str(row.get("user_id") or "") == uid]


async def get_subscription(user_id: int | str, sub_id: int) -> dict[str, Any] | None:
    rows = await _read_subscription_rows()
    index = _find_subscription_index(rows, user_id, sub_id)
    if index < 0:
        return None
    return rows[index]


async def update_subscription(
    sub_id: int,
    user_id: int | str,
    payload: dict[str, Any],
) -> bool:
    rows = await _read_subscription_rows()
    index = _find_subscription_index(rows, user_id, sub_id)
    if index < 0:
        return False
    current = rows[index]
    updated = _validate_subscription_payload(
        user_id,
        payload,
        existing=current,
        subscription_id=sub_id,
    )
    _assert_unique_subscription(rows, updated, ignore_id=sub_id)
    rows[index] = updated
    await _write_subscription_rows(rows)
    return True


async def delete_subscription(user_id: int | str, sub_id: int) -> bool:
    rows = await _read_subscription_rows()
    index = _find_subscription_index(rows, user_id, sub_id)
    if index < 0:
        return False
    rows.pop(index)
    await _write_subscription_rows(rows)
    return True


async def list_all_subscriptions() -> list[dict[str, Any]]:
    return await _read_subscription_rows()


async def list_feed_subscriptions() -> list[dict[str, Any]]:
    return await _read_subscription_rows()


async def update_feed_subscription_state(
    user_id: int | str,
    sub_id: int,
    *,
    last_entry_hash: str,
    last_etag: str | None = None,
    last_modified: str | None = None,
) -> bool:
    rows = await _read_subscription_rows()
    index = _find_subscription_index(rows, user_id, sub_id)
    if index < 0:
        return False
    target = rows[index]
    target["last_entry_hash"] = str(last_entry_hash or "").strip()
    target["last_etag"] = str(last_etag or "").strip()
    target["last_modified"] = str(last_modified or "").strip()
    rows[index] = target
    await _write_subscription_rows(rows)
    return True


async def get_user_subscriptions(user_id: int | str) -> list[dict[str, Any]]:
    return await list_subscriptions(user_id)


async def get_all_subscriptions() -> list[dict[str, Any]]:
    return await list_all_subscriptions()


def _reminders_path(user_id: int | str):
    return user_path(user_id, "automation", "reminders.md")


def _normalize_reminder(raw: dict[str, Any], *, user_id: int | str) -> dict[str, Any]:
    return {
        "id": int(raw.get("id") or 0),
        "user_id": str(user_id),
        "chat_id": str(raw.get("chat_id") or ""),
        "message": str(raw.get("message") or ""),
        "trigger_time": str(raw.get("trigger_time") or ""),
        "created_at": str(raw.get("created_at") or now_iso()),
        "platform": str(raw.get("platform") or "telegram"),
    }


async def _read_user_reminders(user_id: int | str) -> list[dict[str, Any]]:
    data = await read_json(_reminders_path(user_id), [])
    rows: object = data
    if isinstance(data, dict):
        rows = data.get("reminders")
    if not isinstance(rows, list):
        return []
    return [
        _normalize_reminder(item, user_id=user_id)
        for item in rows
        if isinstance(item, dict)
    ]


async def _write_user_reminders(user_id: int | str, rows: list[dict[str, Any]]) -> None:
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "id": int(row.get("id") or 0),
                "chat_id": str(row.get("chat_id") or ""),
                "message": str(row.get("message") or ""),
                "trigger_time": str(row.get("trigger_time") or ""),
                "created_at": str(row.get("created_at") or now_iso()),
                "platform": str(row.get("platform") or "telegram"),
            }
        )
    await write_json(_reminders_path(user_id), payload)


async def add_reminder(
    user_id: int | str,
    chat_id: int | str,
    message: str,
    trigger_time: str,
    platform: str = "telegram",
) -> int:
    uid = str(user_id)
    rows = await _read_user_reminders(uid)
    rid = await next_id("reminder", start=1)
    rows.append(
        {
            "id": int(rid),
            "user_id": uid,
            "chat_id": str(chat_id),
            "message": str(message or ""),
            "trigger_time": str(trigger_time or ""),
            "created_at": now_iso(),
            "platform": str(platform or "telegram"),
        }
    )
    await _write_user_reminders(uid, rows)
    return int(rid)


async def delete_reminder(reminder_id: int, user_id: int | str | None = None):
    rid = int(reminder_id)
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        rows = await _read_user_reminders(uid)
        kept = [item for item in rows if int(item.get("id") or 0) != rid]
        if len(kept) != len(rows):
            await _write_user_reminders(uid, kept)
            return


async def get_pending_reminders(
    user_id: int | str | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        merged.extend(await _read_user_reminders(uid))
    return sorted(merged, key=lambda item: str(item.get("trigger_time") or ""))


def _scheduled_tasks_path(user_id: int | str):
    return user_path(user_id, "automation", "scheduled_tasks.md")


def _normalize_scheduled_task(
    raw: dict[str, Any], *, user_id: int | str
) -> dict[str, Any]:
    return {
        "id": int(raw.get("id") or 0),
        "user_id": str(user_id),
        "crontab": str(raw.get("crontab") or "").strip(),
        "instruction": str(raw.get("instruction") or "").strip(),
        "platform": str(raw.get("platform") or "telegram").strip() or "telegram",
        "need_push": bool(raw.get("need_push", True)),
        "is_active": bool(raw.get("is_active", True)),
        "created_at": str(raw.get("created_at") or now_iso()),
        "updated_at": str(raw.get("updated_at") or now_iso()),
    }


async def _read_user_scheduled_tasks(user_id: int | str) -> list[dict[str, Any]]:
    data = await read_json(_scheduled_tasks_path(user_id), [])
    rows: object = data
    if isinstance(data, dict):
        if isinstance(data.get("scheduled_tasks"), list):
            rows = data.get("scheduled_tasks")
        else:
            rows = data.get("tasks")
    if not isinstance(rows, list):
        return []
    return [
        _normalize_scheduled_task(item, user_id=user_id)
        for item in rows
        if isinstance(item, dict)
    ]


async def _write_user_scheduled_tasks(
    user_id: int | str, rows: list[dict[str, Any]]
) -> None:
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "id": int(row.get("id") or 0),
                "crontab": str(row.get("crontab") or "").strip(),
                "instruction": str(row.get("instruction") or "").strip(),
                "platform": str(row.get("platform") or "telegram"),
                "need_push": bool(row.get("need_push", True)),
                "is_active": bool(row.get("is_active", True)),
                "created_at": str(row.get("created_at") or now_iso()),
                "updated_at": str(row.get("updated_at") or now_iso()),
            }
        )
    await write_json(_scheduled_tasks_path(user_id), payload)


async def add_scheduled_task(
    crontab: str,
    instruction: str,
    user_id: int | str = 0,
    platform: str = "telegram",
    need_push: bool = True,
) -> int:
    uid = str(user_id or "0")
    rows = await _read_user_scheduled_tasks(uid)
    tid = await next_id("scheduled_task", start=1)
    rows.append(
        {
            "id": int(tid),
            "user_id": uid,
            "crontab": str(crontab or "").strip(),
            "instruction": str(instruction or "").strip(),
            "platform": str(platform or "telegram"),
            "need_push": bool(need_push),
            "is_active": True,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    await _write_user_scheduled_tasks(uid, rows)
    return int(tid)


async def get_all_active_tasks(
    user_id: int | str | None = None,
) -> list[dict[str, Any]]:
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    merged: list[dict[str, Any]] = []
    for uid in target_users:
        rows = await _read_user_scheduled_tasks(uid)
        merged.extend([item for item in rows if bool(item.get("is_active", True))])
    return merged


async def update_task_status(
    task_id: int, is_active: bool, user_id: int | str | None = None
):
    tid = int(task_id)
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        rows = await _read_user_scheduled_tasks(uid)
        changed = False
        for item in rows:
            if int(item.get("id") or 0) != tid:
                continue
            item["is_active"] = bool(is_active)
            item["updated_at"] = now_iso()
            changed = True
            break
        if changed:
            await _write_user_scheduled_tasks(uid, rows)
            return


async def delete_task(task_id: int, user_id: int | str | None = None):
    tid = int(task_id)
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        rows = await _read_user_scheduled_tasks(uid)
        kept = [item for item in rows if int(item.get("id") or 0) != tid]
        if len(kept) != len(rows):
            await _write_user_scheduled_tasks(uid, kept)
            return


async def update_scheduled_task(
    task_id: int,
    user_id: int | str | None = None,
    crontab: str | None = None,
    instruction: str | None = None,
) -> bool:
    tid = int(task_id)
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    for uid in target_users:
        rows = await _read_user_scheduled_tasks(uid)
        changed = False
        for item in rows:
            if int(item.get("id") or 0) != tid:
                continue
            if crontab is not None:
                item["crontab"] = str(crontab).strip()
            if instruction is not None:
                item["instruction"] = str(instruction).strip()
            item["updated_at"] = now_iso()
            changed = True
            break
        if changed:
            await _write_user_scheduled_tasks(uid, rows)
            return True
    return False


def _watchlist_path(user_id: int | str):
    return user_path(user_id, "stock", "watchlist.md")


def _normalize_watchlist_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": str(raw.get("stock_code") or "").strip(),
        "stock_name": str(raw.get("stock_name") or "").strip(),
        "platform": str(raw.get("platform") or "telegram").strip() or "telegram",
    }


def _to_watchlist_runtime_rows(
    user_id: int | str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    runtime: list[dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        code = str(item.get("stock_code") or "").strip()
        if not code:
            continue
        runtime.append(
            {
                "id": index,
                "user_id": str(user_id),
                "stock_code": code,
                "stock_name": str(item.get("stock_name") or code),
                "platform": str(item.get("platform") or "telegram"),
            }
        )
    return runtime


async def _read_watchlist(user_id: int | str) -> list[dict[str, Any]]:
    data = await read_json(_watchlist_path(user_id), [])
    if not isinstance(data, list):
        return []
    rows = [_normalize_watchlist_row(item) for item in data if isinstance(item, dict)]
    return [item for item in rows if item.get("stock_code")]


async def _write_watchlist(user_id: int | str, rows: list[dict[str, Any]]) -> None:
    payload: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("stock_code") or "").strip()
        if not code:
            continue
        payload.append(
            {
                "stock_code": code,
                "stock_name": str(row.get("stock_name") or code).strip(),
                "platform": str(row.get("platform") or "telegram").strip()
                or "telegram",
            }
        )
    await write_json(_watchlist_path(user_id), payload)


async def add_watchlist_stock(
    user_id: int | str,
    stock_code: str,
    stock_name: str,
    platform: str = "telegram",
) -> bool:
    rows = await _read_watchlist(user_id)
    code = str(stock_code or "").strip()
    if not code:
        return False
    if any(str(item.get("stock_code") or "").strip() == code for item in rows):
        return False

    rows.append(
        {
            "stock_code": code,
            "stock_name": str(stock_name or code).strip(),
            "platform": str(platform or "telegram"),
        }
    )
    await _write_watchlist(user_id, rows)
    return True


async def remove_watchlist_stock(user_id: int | str, stock_code: str) -> bool:
    rows = await _read_watchlist(user_id)
    code = str(stock_code or "").strip()
    kept = [item for item in rows if str(item.get("stock_code") or "").strip() != code]
    changed = len(kept) != len(rows)
    if changed:
        await _write_watchlist(user_id, kept)
    return changed


async def get_user_watchlist(
    user_id: int | str, platform: str | None = None
) -> list[dict[str, Any]]:
    rows = await _read_watchlist(user_id)
    if platform:
        target = str(platform).strip().lower()
        rows = [
            item
            for item in rows
            if str(item.get("platform") or "telegram").strip().lower() == target
        ]
    return _to_watchlist_runtime_rows(user_id, rows)


async def get_all_watchlist_users() -> list[tuple[int | str, str]]:
    pairs: list[tuple[int | str, str]] = []
    seen: set[tuple[str, str]] = set()
    for uid in all_user_ids():
        rows = await _read_watchlist(uid)
        for row in rows:
            plat = str(row.get("platform") or "telegram")
            key = (str(uid), plat)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((uid, plat))
    return pairs


def _settings_path(user_id: int | str):
    return user_path(user_id, "settings.md")


def _default_user_settings(user_id: int | str) -> dict[str, object]:
    return {
        "user_id": str(user_id),
        "auto_translate": 0,
        "target_lang": "zh-CN",
        "updated_at": now_iso(),
    }


async def set_translation_mode(user_id: int | str, enabled: bool):
    path = _settings_path(user_id)
    current = await read_json(path, _default_user_settings(user_id))
    if isinstance(current, dict):
        settings: dict[str, object] = {
            str(key): value for key, value in current.items()
        }
    else:
        settings = _default_user_settings(user_id)
    settings["user_id"] = str(user_id)
    settings["auto_translate"] = 1 if bool(enabled) else 0
    settings["target_lang"] = str(settings.get("target_lang") or "zh-CN")
    settings["updated_at"] = now_iso()
    await write_json(path, settings)


async def get_user_settings(user_id: int | str) -> dict[str, str | int]:
    path = _settings_path(user_id)
    current = await read_json(path, {})
    settings: dict[str, object] = (
        {str(key): value for key, value in current.items()}
        if isinstance(current, dict)
        else {}
    )
    return {
        "user_id": str(user_id),
        "auto_translate": int(
            cast(int | str | None, settings.get("auto_translate")) or 0
        ),
        "target_lang": str(settings.get("target_lang") or "zh-CN"),
        "updated_at": str(settings.get("updated_at") or now_iso()),
    }
