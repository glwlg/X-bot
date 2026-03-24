import importlib
import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
SINGLE_USER_SCOPE = _state_paths.SINGLE_USER_SCOPE
all_user_ids = _state_paths.all_user_ids
shared_user_path = _state_paths.shared_user_path
system_path = _state_paths.system_path
user_path = _state_paths.user_path

logger = logging.getLogger(__name__)

_ENTRY_RE = re.compile(r"^###\s+(system|user|model)\s*\n```text\n(.*?)\n```", re.M | re.S)
_VISIBLE_CHAT_ROLES = {"user", "model"}
_SUPPORTED_CHAT_ROLES = _VISIBLE_CHAT_ROLES | {"system"}


def _normalized_user_id(value: int | str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_row_list(data: Any, *keys: str) -> list[dict[str, Any]]:
    rows: object = data
    if isinstance(data, dict):
        fallback_rows: object | None = None
        for key in keys:
            candidate = data.get(key)
            if isinstance(candidate, list):
                if candidate:
                    rows = candidate
                    break
                if fallback_rows is None:
                    fallback_rows = candidate
        else:
            if fallback_rows is not None:
                rows = fallback_rows
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]


def _row_user_id(raw: dict[str, Any]) -> str:
    return str(raw.get("user_id") or "").strip()


def _merge_user_ids(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for user_id in group:
            uid = _normalized_user_id(user_id)
            if not uid or uid in seen:
                continue
            seen.add(uid)
            merged.append(uid)
    return merged


def _merge_unique_rows(
    current_rows: list[dict[str, Any]],
    legacy_rows: list[dict[str, Any]],
    *,
    key_fn,
) -> list[dict[str, Any]]:
    merged = list(current_rows)
    seen = {key_fn(row) for row in current_rows}
    for row in legacy_rows:
        key = key_fn(row)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _dedupe_rows(
    rows: list[dict[str, Any]],
    *,
    key_fn,
) -> list[dict[str, Any]]:
    order: list[Any] = []
    latest: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = key_fn(row)
        if key not in latest:
            order.append(key)
        latest[key] = row
    return [latest[key] for key in order]


def _max_row_id(rows: list[dict[str, Any]]) -> int:
    highest = 0
    for row in rows:
        try:
            highest = max(highest, int(row.get("id") or 0))
        except Exception:
            continue
    return highest


_FEATURE_DELIVERY_NAMES = {"rss", "stock"}


def _feature_delivery_targets_path(user_id: int | str):
    _ = user_id
    return user_path(user_id, "automation", "delivery_targets.md")


def _normalize_delivery_target(raw: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(raw or {})
    return {
        "platform": normalize_platform(payload.get("platform")),
        "chat_id": str(payload.get("chat_id") or "").strip(),
        "updated_at": str(payload.get("updated_at") or now_iso()),
    }


def _normalize_feature_delivery_name(feature: str) -> str:
    normalized = str(feature or "").strip().lower()
    if normalized not in _FEATURE_DELIVERY_NAMES:
        raise ValueError(f"unsupported feature delivery target: {feature}")
    return normalized


async def _read_feature_delivery_targets(user_id: int | str) -> dict[str, dict[str, str]]:
    data = await read_json(_feature_delivery_targets_path(user_id), {})
    if not isinstance(data, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for feature in _FEATURE_DELIVERY_NAMES:
        raw_target = data.get(feature)
        if not isinstance(raw_target, dict):
            continue
        target = _normalize_delivery_target(raw_target)
        if target["platform"] and target["chat_id"]:
            normalized[feature] = target
    return normalized


async def _write_feature_delivery_targets(
    user_id: int | str,
    targets: dict[str, dict[str, str]],
) -> None:
    payload: dict[str, dict[str, str]] = {}
    for feature, target in dict(targets or {}).items():
        if feature not in _FEATURE_DELIVERY_NAMES or not isinstance(target, dict):
            continue
        normalized = _normalize_delivery_target(target)
        if not normalized["platform"] or not normalized["chat_id"]:
            continue
        payload[feature] = normalized
    await write_json(_feature_delivery_targets_path(user_id), payload)


async def list_feature_delivery_targets(
    user_id: int | str,
) -> dict[str, dict[str, str]]:
    return await _read_feature_delivery_targets(user_id)


async def get_feature_delivery_target(
    user_id: int | str,
    feature: str,
) -> dict[str, str]:
    feature_name = _normalize_feature_delivery_name(feature)
    targets = await _read_feature_delivery_targets(user_id)
    return dict(targets.get(feature_name) or {})


async def set_feature_delivery_target(
    user_id: int | str,
    feature: str,
    platform: str,
    chat_id: str,
) -> dict[str, str]:
    feature_name = _normalize_feature_delivery_name(feature)
    targets = await _read_feature_delivery_targets(user_id)
    normalized = _normalize_delivery_target(
        {
            "platform": platform,
            "chat_id": chat_id,
            "updated_at": now_iso(),
        }
    )
    if not normalized["platform"] or not normalized["chat_id"]:
        raise ValueError("platform and chat_id are required")
    targets[feature_name] = normalized
    await _write_feature_delivery_targets(user_id, targets)
    return normalized


async def _next_id_after_legacy_rows(
    counter_name: str,
    legacy_path: Path,
    *,
    start: int = 1,
    list_keys: tuple[str, ...] = (),
) -> int:
    legacy_rows = _read_row_list(await read_json(legacy_path, []), *list_keys)
    return await next_id(counter_name, start=max(start, _max_row_id(legacy_rows) + 1))


async def _next_id_after_store_rows(
    counter_name: str,
    path: Path,
    *,
    start: int = 1,
    list_keys: tuple[str, ...] = (),
) -> int:
    rows = _read_row_list(await read_json(path, []), *list_keys)
    return await next_id(counter_name, start=max(start, _max_row_id(rows) + 1))


async def _delete_legacy_rows(
    path: Path,
    *,
    user_id: int | str,
    predicate,
    list_keys: tuple[str, ...] = (),
) -> None:
    payload = await read_json(path, [])
    target_user_id = _normalized_user_id(user_id)

    if isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
        kept = [
            item
            for item in rows
            if _row_user_id(item) != target_user_id or not predicate(item)
        ]
        if len(kept) != len(rows):
            await write_json(path, kept)
        return

    if not isinstance(payload, dict):
        return

    updated = dict(payload)
    changed = False
    for key in list_keys:
        rows = updated.get(key)
        if not isinstance(rows, list):
            continue
        kept = [
            item
            for item in rows
            if not isinstance(item, dict)
            or _row_user_id(item) != target_user_id
            or not predicate(item)
        ]
        if len(kept) == len(rows):
            continue
        updated[key] = kept
        changed = True
    if changed:
        await write_json(path, updated)


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


def _subs_path(user_id: int | str) -> Path:
    _ = user_id
    return user_path(user_id, "rss", "subscriptions.md")


def _normalize_subscription(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    try:
        sub_id = int(raw.get("id") or 0)
    except Exception:
        return None
    if sub_id <= 0:
        return None

    feed_url = str(raw.get("feed_url") or "").strip()
    if not feed_url:
        return None

    provider_value = raw.get("provider")
    if str(provider_value or "").strip().lower() == "rss":
        provider_value = "native_rss"

    try:
        provider = normalize_provider(provider_value, feed_url=feed_url)
    except ValueError:
        return None

    title = str(raw.get("title") or "").strip()
    if not title:
        title = default_title(feed_url=feed_url)

    return {
        "id": sub_id,
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


async def _read_current_subscription_rows(user_id: int | str) -> list[dict[str, Any]]:
    _ = user_id
    data = await read_json(_subs_path(user_id), [])
    rows: list[dict[str, Any]] = []
    for item in _read_row_list(data):
        normalized = _normalize_subscription(item)
        if normalized is not None:
            rows.append(normalized)
    return rows


async def _read_legacy_subscription_rows(user_id: int | str) -> list[dict[str, Any]]:
    _ = user_id
    return []


async def _read_subscription_rows(user_id: int | str) -> list[dict[str, Any]]:
    return _dedupe_rows(
        await _read_current_subscription_rows(user_id),
        key_fn=lambda row: int(row.get("id") or 0),
    )


async def _legacy_subscription_user_ids() -> list[str]:
    return []


async def _write_subscription_rows(
    user_id: int | str, rows: list[dict[str, Any]]
) -> None:
    _ = user_id
    payload: list[dict[str, Any]] = []
    for row in _dedupe_rows(rows, key_fn=lambda item: int(item.get("id") or 0)):
        payload.append(_serialize_subscription(row))
    payload.sort(key=lambda item: int(item["id"]))
    await write_json(_subs_path(user_id), payload)


def _find_subscription_index(
    rows: list[dict[str, Any]], user_id: int | str, sub_id: int
) -> int:
    _ = user_id
    for index, row in enumerate(rows):
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
        if (
            str(row.get("feed_url") or "").strip()
            == str(candidate.get("feed_url") or "").strip()
        ):
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
        "provider": provider,
        "title": title,
        "platform": platform,
        "feed_url": feed_url,
        "last_etag": str(source.get("last_etag") or "").strip(),
        "last_modified": str(source.get("last_modified") or "").strip(),
        "last_entry_hash": str(source.get("last_entry_hash") or "").strip(),
    }


async def create_subscription(
    user_id: int | str, payload: dict[str, Any]
) -> dict[str, Any]:
    rows = await _read_subscription_rows(user_id)
    sub_id = await _next_id_after_store_rows(
        "subscriptions",
        _subs_path(""),
    )
    record = _validate_subscription_payload(user_id, payload, subscription_id=sub_id)
    _assert_unique_subscription(rows, record)
    rows.append(record)
    await _write_subscription_rows(user_id, rows)
    return record


async def list_subscriptions(user_id: int | str) -> list[dict[str, Any]]:
    return await _read_subscription_rows(user_id)


async def get_subscription(user_id: int | str, sub_id: int) -> dict[str, Any] | None:
    rows = await _read_subscription_rows(user_id)
    index = _find_subscription_index(rows, user_id, sub_id)
    if index < 0:
        return None
    return rows[index]


async def update_subscription(
    sub_id: int,
    user_id: int | str,
    payload: dict[str, Any],
) -> bool:
    rows = await _read_subscription_rows(user_id)
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
    await _write_subscription_rows(user_id, rows)
    return True


async def delete_subscription(user_id: int | str, sub_id: int) -> bool:
    rows = await _read_subscription_rows(user_id)
    index = _find_subscription_index(rows, user_id, sub_id)
    if index < 0:
        return False
    rows.pop(index)
    await _write_subscription_rows(user_id, rows)
    return True


async def list_all_subscriptions() -> list[dict[str, Any]]:
    return await _read_subscription_rows("")


async def list_feed_subscriptions() -> list[dict[str, Any]]:
    return await list_all_subscriptions()


async def update_feed_subscription_state(
    user_id: int | str,
    sub_id: int,
    *,
    last_entry_hash: str,
    last_etag: str | None = None,
    last_modified: str | None = None,
) -> bool:
    rows = await _read_subscription_rows(user_id)
    index = _find_subscription_index(rows, user_id, sub_id)
    if index < 0:
        return False
    target = rows[index]
    target["last_entry_hash"] = str(last_entry_hash or "").strip()
    target["last_etag"] = str(last_etag or "").strip()
    target["last_modified"] = str(last_modified or "").strip()
    rows[index] = target
    await _write_subscription_rows(user_id, rows)
    return True


async def get_user_subscriptions(user_id: int | str) -> list[dict[str, Any]]:
    return await list_subscriptions(user_id)


async def get_all_subscriptions() -> list[dict[str, Any]]:
    return await list_all_subscriptions()


def _reminders_path(user_id: int | str):
    _ = user_id
    return user_path(user_id, "automation", "reminders.md")


def _normalize_reminder(raw: dict[str, Any], *, user_id: int | str) -> dict[str, Any]:
    _ = user_id
    return {
        "id": int(raw.get("id") or 0),
        "chat_id": str(raw.get("chat_id") or ""),
        "message": str(raw.get("message") or ""),
        "trigger_time": str(raw.get("trigger_time") or ""),
        "created_at": str(raw.get("created_at") or now_iso()),
        "platform": str(raw.get("platform") or "telegram"),
    }


async def _read_user_reminders(user_id: int | str) -> list[dict[str, Any]]:
    current_rows = _read_row_list(await read_json(_reminders_path(user_id), []), "reminders")
    return _dedupe_rows(
        [
            _normalize_reminder(item, user_id=user_id)
            for item in current_rows
            if isinstance(item, dict)
        ],
        key_fn=lambda row: int(row.get("id") or 0),
    )


async def _legacy_reminder_user_ids() -> list[str]:
    return []


async def _write_user_reminders(user_id: int | str, rows: list[dict[str, Any]]) -> None:
    payload: list[dict[str, Any]] = []
    for row in _dedupe_rows(rows, key_fn=lambda item: int(item.get("id") or 0)):
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
    rows = await _read_user_reminders(user_id)
    rid = await _next_id_after_store_rows(
        "reminder",
        _reminders_path(""),
        list_keys=("reminders",),
    )
    rows.append(
        {
            "id": int(rid),
            "chat_id": str(chat_id),
            "message": str(message or ""),
            "trigger_time": str(trigger_time or ""),
            "created_at": now_iso(),
            "platform": str(platform or "telegram"),
        }
    )
    await _write_user_reminders(user_id, rows)
    return int(rid)


async def delete_reminder(reminder_id: int, user_id: int | str | None = None):
    rid = int(reminder_id)
    rows = await _read_user_reminders(user_id or "")
    kept = [item for item in rows if int(item.get("id") or 0) != rid]
    if len(kept) != len(rows):
        await _write_user_reminders(user_id or "", kept)


async def get_pending_reminders(
    user_id: int | str | None = None,
) -> list[dict[str, Any]]:
    return sorted(
        await _read_user_reminders(user_id or ""),
        key=lambda item: str(item.get("trigger_time") or ""),
    )


def _scheduled_tasks_path(user_id: int | str):
    _ = user_id
    return user_path(user_id, "automation", "scheduled_tasks.md")


def _normalize_scheduled_task(
    raw: dict[str, Any], *, user_id: int | str
) -> dict[str, Any]:
    _ = user_id
    return {
        "id": int(raw.get("id") or 0),
        "crontab": str(raw.get("crontab") or "").strip(),
        "instruction": str(raw.get("instruction") or "").strip(),
        "platform": str(raw.get("platform") or "telegram").strip() or "telegram",
        "chat_id": str(raw.get("chat_id") or "").strip(),
        "session_id": str(raw.get("session_id") or "").strip(),
        "need_push": bool(raw.get("need_push", True)),
        "is_active": bool(raw.get("is_active", True)),
        "created_at": str(raw.get("created_at") or now_iso()),
        "updated_at": str(raw.get("updated_at") or now_iso()),
    }


async def _read_user_scheduled_tasks(user_id: int | str) -> list[dict[str, Any]]:
    current_rows = _read_row_list(
        await read_json(_scheduled_tasks_path(user_id), []),
        "scheduled_tasks",
        "tasks",
    )
    normalized_current = [
        _normalize_scheduled_task(item, user_id=user_id)
        for item in current_rows
        if isinstance(item, dict)
    ]
    return _dedupe_rows(
        normalized_current,
        key_fn=lambda row: int(row.get("id") or 0),
    )


async def _legacy_scheduled_task_user_ids() -> list[str]:
    return []


async def _write_user_scheduled_tasks(
    user_id: int | str, rows: list[dict[str, Any]]
) -> None:
    payload: list[dict[str, Any]] = []
    for row in _dedupe_rows(rows, key_fn=lambda item: int(item.get("id") or 0)):
        payload.append(
            {
                "id": int(row.get("id") or 0),
                "crontab": str(row.get("crontab") or "").strip(),
                "instruction": str(row.get("instruction") or "").strip(),
                "platform": str(row.get("platform") or "telegram"),
            }
        )
        if str(row.get("chat_id") or "").strip():
            payload[-1]["chat_id"] = str(row.get("chat_id") or "").strip()
        if str(row.get("session_id") or "").strip():
            payload[-1]["session_id"] = str(row.get("session_id") or "").strip()
        payload[-1]["need_push"] = bool(row.get("need_push", True))
        payload[-1]["is_active"] = bool(row.get("is_active", True))
        payload[-1]["created_at"] = str(row.get("created_at") or now_iso())
        payload[-1]["updated_at"] = str(row.get("updated_at") or now_iso())
    payload.sort(key=lambda item: int(item.get("id") or 0))
    await write_json(_scheduled_tasks_path(user_id), payload)


async def add_scheduled_task(
    crontab: str,
    instruction: str,
    user_id: int | str = 0,
    platform: str = "telegram",
    chat_id: str = "",
    session_id: str = "",
    need_push: bool = True,
) -> int:
    rows = await _read_user_scheduled_tasks(user_id or "")
    tid = await _next_id_after_store_rows(
        "scheduled_task",
        _scheduled_tasks_path(""),
        list_keys=("scheduled_tasks", "tasks"),
    )
    rows.append(
        {
            "id": int(tid),
            "crontab": str(crontab or "").strip(),
            "instruction": str(instruction or "").strip(),
            "platform": str(platform or "telegram"),
            "chat_id": str(chat_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "need_push": bool(need_push),
            "is_active": True,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    await _write_user_scheduled_tasks(user_id or "", rows)
    return int(tid)


async def get_all_active_tasks(
    user_id: int | str | None = None,
) -> list[dict[str, Any]]:
    rows = await _read_user_scheduled_tasks(user_id or "")
    return [item for item in rows if bool(item.get("is_active", True))]


async def update_task_status(
    task_id: int, is_active: bool, user_id: int | str | None = None
):
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
    changed = False
    for item in rows:
        if int(item.get("id") or 0) != tid:
            continue
        item["is_active"] = bool(is_active)
        item["updated_at"] = now_iso()
        changed = True
        break
    if changed:
        await _write_user_scheduled_tasks(user_id or "", rows)


async def update_task_delivery_target(
    task_id: int,
    user_id: int | str | None = None,
    *,
    platform: str,
    chat_id: str,
    session_id: str = "",
) -> bool:
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
    changed = False
    for item in rows:
        if int(item.get("id") or 0) != tid:
            continue
        item["platform"] = str(platform or "telegram").strip() or "telegram"
        item["chat_id"] = str(chat_id or "").strip()
        item["session_id"] = str(session_id or "").strip()
        item["updated_at"] = now_iso()
        changed = True
        break
    if changed:
        await _write_user_scheduled_tasks(user_id or "", rows)
        return True
    return False


async def delete_task(task_id: int, user_id: int | str | None = None):
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
    kept = [item for item in rows if int(item.get("id") or 0) != tid]
    if len(kept) != len(rows):
        await _write_user_scheduled_tasks(user_id or "", kept)


async def update_scheduled_task(
    task_id: int,
    user_id: int | str | None = None,
    crontab: str | None = None,
    instruction: str | None = None,
) -> bool:
    tid = int(task_id)
    rows = await _read_user_scheduled_tasks(user_id or "")
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
        await _write_user_scheduled_tasks(user_id or "", rows)
        return True
    return False


def _watchlist_path(user_id: int | str):
    _ = user_id
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
                "stock_code": code,
                "stock_name": str(item.get("stock_name") or code),
                "platform": str(item.get("platform") or "telegram"),
            }
        )
    return runtime


async def _read_watchlist(user_id: int | str) -> list[dict[str, Any]]:
    _ = user_id
    current_rows = _read_row_list(await read_json(_watchlist_path(user_id), []))
    normalized_current: list[dict[str, Any]] = []
    for raw in current_rows:
        normalized = _normalize_watchlist_row(raw)
        if normalized.get("stock_code"):
            normalized_current.append(normalized)
    return _dedupe_rows(
        normalized_current,
        key_fn=lambda row: (
            str(row.get("stock_code") or "").strip().lower(),
            str(row.get("platform") or "telegram").strip().lower(),
        ),
    )


async def _legacy_watchlist_user_ids() -> list[str]:
    return []


async def _write_watchlist(user_id: int | str, rows: list[dict[str, Any]]) -> None:
    payload: list[dict[str, Any]] = []
    for row in _dedupe_rows(
        rows,
        key_fn=lambda item: (
            str(item.get("stock_code") or "").strip().lower(),
            str(item.get("platform") or "telegram").strip().lower(),
        ),
    ):
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
    else:
        rows = _dedupe_rows(
            rows,
            key_fn=lambda row: str(row.get("stock_code") or "").strip().lower(),
        )
    return _to_watchlist_runtime_rows(user_id, rows)


async def get_all_watchlist_users() -> list[tuple[int | str, str]]:
    rows = await _read_watchlist("")
    if not rows:
        return []
    target = await get_feature_delivery_target("", "stock")
    platform = str(target.get("platform") or rows[0].get("platform") or "telegram")
    return [(SINGLE_USER_SCOPE, platform)]
