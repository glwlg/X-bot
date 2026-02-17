"""Repository filesystem backend (SQLite-free runtime).

This module keeps legacy API symbols (`init_db`, `get_db`, `DB_PATH`) for
compatibility, while actual persistence uses Markdown files with YAML blocks.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
import yaml

from core.config import DATA_DIR

logger = logging.getLogger(__name__)

# Compatibility constant kept for older imports/tests.
DB_PATH = str((Path(DATA_DIR) / "bot_data.db").resolve())

_LOCKS: dict[str, asyncio.Lock] = {}
_COUNTERS_FILE = "id_counters.md"


def _runtime_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()


def _runtime_db_path() -> Path:
    override = str(DB_PATH or "").strip()
    if override:
        return Path(override).resolve()
    return (_runtime_data_dir() / "bot_data.db").resolve()


def users_root() -> Path:
    root = (_runtime_data_dir() / "users").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def repo_root() -> Path:
    root = (_runtime_data_dir() / "system" / "repositories").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_part(value: Any, fallback: str = "unknown") -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    safe = re.sub(r"[^a-zA-Z0-9_\-:.]+", "_", raw)
    return safe or fallback


def user_path(user_id: int | str, *parts: str) -> Path:
    uid = _safe_part(user_id)
    path = (users_root() / uid).resolve()
    for part in parts:
        path = (path / str(part)).resolve()
    return path


def system_path(*parts: str) -> Path:
    path = repo_root()
    for part in parts:
        path = (path / str(part)).resolve()
    return path


def all_user_ids() -> list[str]:
    root = users_root()
    ids: list[str] = []
    for item in root.iterdir():
        if item.is_dir():
            ids.append(item.name)
    return sorted(ids)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path.resolve())
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _extract_yaml_block(text: str) -> str:
    raw = str(text or "")
    fence = re.search(r"```yaml\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        return str(fence.group(1) or "")
    front = re.search(r"^---\s*\n(.*?)\n---\s*$", raw, flags=re.DOTALL)
    if front:
        return str(front.group(1) or "")
    return raw


def _read_json_sync(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return copy.deepcopy(default)
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return copy.deepcopy(default)
        yaml_text = _extract_yaml_block(text)
        loaded = yaml.safe_load(yaml_text)
        if loaded is None:
            return copy.deepcopy(default)
        return loaded
    except Exception:
        return copy.deepcopy(default)


def _write_json_sync(path: Path, payload: Any) -> None:
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    title = path.stem.replace("_", " ").strip().title() or "Data"
    body = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    content = (
        f"# {title}\n\n"
        "<!-- x-bot-state-file: edit via read/write/edit when needed -->\n"
        "<!-- payload format: fenced YAML block below -->\n\n"
        f"```yaml\n{body}\n```\n"
    )
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


async def read_json(path: Path, default: Any) -> Any:
    lock = _lock_for(path)
    async with lock:
        return _read_json_sync(path, default)


async def write_json(path: Path, payload: Any) -> None:
    lock = _lock_for(path)
    async with lock:
        _write_json_sync(path, payload)


async def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    lock = _lock_for(path)
    async with lock:
        current = _read_json_sync(path, [])
        if not isinstance(current, list):
            current = []
        current.append(dict(row or {}))
        _write_json_sync(path, current)


async def read_jsonl(path: Path) -> list[dict[str, Any]]:
    lock = _lock_for(path)
    async with lock:
        current = _read_json_sync(path, [])
        if not isinstance(current, list):
            return []
        return [item for item in current if isinstance(item, dict)]


async def next_id(counter_name: str, start: int = 1) -> int:
    path = system_path(_COUNTERS_FILE)
    lock = _lock_for(path)
    async with lock:
        payload = _read_json_sync(path, {})
        if not isinstance(payload, dict):
            payload = {}
        key = str(counter_name or "default")
        current = int(payload.get(key, start) or start)
        payload[key] = current + 1
        _write_json_sync(path, payload)
        return current


def _set_counter_min_sync(counter_name: str, min_next: int) -> None:
    path = system_path(_COUNTERS_FILE)
    payload = _read_json_sync(path, {})
    if not isinstance(payload, dict):
        payload = {}
    key = str(counter_name or "default")
    current = int(payload.get(key, 1) or 1)
    if min_next > current:
        payload[key] = int(min_next)
        _write_json_sync(path, payload)


class _DummyCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None):
        self._rows = rows or []
        self.rowcount = 0
        self.lastrowid = 0

    def __await__(self):
        async def _coerce_self():
            return self

        return _coerce_self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _DummyDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    def execute(self, query: str, params: tuple[Any, ...] | None = None):
        del params
        normalized = str(query or "").strip().lower()
        if normalized.startswith("select 1"):
            return _DummyCursor([(1,)])
        return _DummyCursor([])

    async def commit(self):
        return None


async def get_db():
    """Compatibility shim; repositories now use filesystem storage."""
    return _DummyDB()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    )
    return cur.fetchone() is not None


def _rows(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(query)
    return [dict(row) for row in cur.fetchall()]


def _migrate_from_sqlite_once() -> None:
    marker = system_path(".sqlite_migrated.md")
    if marker.exists():
        return

    db_path = _runtime_db_path()
    if not db_path.exists():
        return

    try:
        conn = sqlite3.connect(str(db_path))
    except Exception as exc:
        logger.warning("Failed opening legacy SQLite for migration: %s", exc)
        return

    migrated = {
        "db_path": str(db_path),
        "migrated_at": now_iso(),
        "tables": [],
    }

    try:
        # allowed_users
        if _table_exists(conn, "allowed_users"):
            path = system_path("allowed_users.md")
            if not path.exists():
                _write_json_sync(path, _rows(conn, "SELECT * FROM allowed_users"))
            migrated["tables"].append("allowed_users")

        # video_cache
        if _table_exists(conn, "video_cache"):
            path = system_path("video_cache.md")
            if not path.exists():
                mapping: dict[str, dict[str, Any]] = {}
                for row in _rows(conn, "SELECT * FROM video_cache"):
                    file_id = str(row.get("file_id") or "").strip()
                    if not file_id:
                        continue
                    mapping[file_id] = {
                        "file_path": str(row.get("file_path") or ""),
                        "created_at": str(row.get("created_at") or now_iso()),
                    }
                _write_json_sync(path, mapping)
            migrated["tables"].append("video_cache")

        # user_settings
        if _table_exists(conn, "user_settings"):
            for row in _rows(conn, "SELECT * FROM user_settings"):
                uid = _safe_part(row.get("user_id"))
                path = user_path(uid, "settings.md")
                if path.exists():
                    continue
                _write_json_sync(
                    path,
                    {
                        "user_id": uid,
                        "auto_translate": int(row.get("auto_translate") or 0),
                        "target_lang": str(row.get("target_lang") or "zh-CN"),
                        "updated_at": str(row.get("updated_at") or now_iso()),
                    },
                )
            migrated["tables"].append("user_settings")

        # accounts
        if _table_exists(conn, "accounts"):
            grouped_accounts: dict[str, dict[str, Any]] = {}
            for row in _rows(conn, "SELECT * FROM accounts"):
                uid = _safe_part(row.get("user_id"))
                service = str(row.get("service") or "").strip()
                if not service:
                    continue
                enc_data = row.get("enc_data")
                parsed: dict[str, Any] = {}
                if isinstance(enc_data, str) and enc_data.strip():
                    try:
                        loaded = json.loads(enc_data)
                        if isinstance(loaded, dict):
                            parsed = loaded
                    except Exception:
                        parsed = {}
                if uid not in grouped_accounts:
                    grouped_accounts[uid] = {}
                grouped_accounts[uid][service] = {
                    "data": parsed,
                    "updated_at": str(row.get("updated_at") or now_iso()),
                }
            for uid, data in grouped_accounts.items():
                path = user_path(uid, "accounts.md")
                if not path.exists():
                    _write_json_sync(path, data)
            migrated["tables"].append("accounts")

        # subscriptions
        if _table_exists(conn, "subscriptions"):
            max_id = 0
            grouped_subscriptions: dict[str, list[dict[str, Any]]] = {}
            for row in _rows(conn, "SELECT * FROM subscriptions"):
                uid = _safe_part(row.get("user_id"))
                record = {
                    "id": int(row.get("id") or 0),
                    "user_id": uid,
                    "feed_url": str(row.get("feed_url") or ""),
                    "title": str(row.get("title") or ""),
                    "last_etag": str(row.get("last_etag") or ""),
                    "last_modified": str(row.get("last_modified") or ""),
                    "last_entry_hash": str(row.get("last_entry_hash") or ""),
                    "created_at": str(row.get("created_at") or now_iso()),
                    "platform": str(row.get("platform") or "telegram"),
                }
                max_id = max(max_id, int(record["id"]))
                grouped_subscriptions.setdefault(uid, []).append(record)
            for uid, records in grouped_subscriptions.items():
                path = user_path(uid, "rss", "subscriptions.md")
                if not path.exists():
                    _write_json_sync(path, records)
            _set_counter_min_sync("subscription", max_id + 1)
            migrated["tables"].append("subscriptions")

        # watchlist
        if _table_exists(conn, "watchlist"):
            max_id = 0
            grouped_watchlist: dict[str, list[dict[str, Any]]] = {}
            for row in _rows(conn, "SELECT * FROM watchlist"):
                uid = _safe_part(row.get("user_id"))
                record = {
                    "id": int(row.get("id") or 0),
                    "user_id": uid,
                    "stock_code": str(row.get("stock_code") or ""),
                    "stock_name": str(row.get("stock_name") or ""),
                    "created_at": str(row.get("created_at") or now_iso()),
                    "platform": str(row.get("platform") or "telegram"),
                }
                max_id = max(max_id, int(record["id"]))
                grouped_watchlist.setdefault(uid, []).append(record)
            for uid, records in grouped_watchlist.items():
                path = user_path(uid, "stock", "watchlist.md")
                if not path.exists():
                    _write_json_sync(path, records)
            _set_counter_min_sync("watchlist", max_id + 1)
            migrated["tables"].append("watchlist")

        # chat_history
        if _table_exists(conn, "chat_history"):
            max_id = 0
            grouped_chat: dict[str, list[dict[str, Any]]] = {}
            for row in _rows(conn, "SELECT * FROM chat_history ORDER BY id ASC"):
                uid = _safe_part(row.get("user_id"))
                item = {
                    "id": int(row.get("id") or 0),
                    "user_id": uid,
                    "role": str(row.get("role") or "user"),
                    "content": str(row.get("content") or ""),
                    "created_at": str(row.get("created_at") or now_iso()),
                    "session_id": str(row.get("session_id") or ""),
                }
                max_id = max(max_id, int(item["id"]))
                grouped_chat.setdefault(uid, []).append(item)
            for uid, records in grouped_chat.items():
                path = user_path(uid, "chat", "history.md")
                if not path.exists():
                    _write_json_sync(path, records)
            _set_counter_min_sync("chat_message", max_id + 1)
            migrated["tables"].append("chat_history")

        # reminders -> per user automation/reminders.md
        if _table_exists(conn, "reminders"):
            rows = _rows(conn, "SELECT * FROM reminders")
            max_id = 0
            grouped_reminders: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                rid = int(row.get("id") or 0)
                max_id = max(max_id, rid)
                uid = _safe_part(row.get("user_id"))
                grouped_reminders.setdefault(uid, []).append(
                    {
                        "id": rid,
                        "chat_id": str(row.get("chat_id") or ""),
                        "message": str(row.get("message") or ""),
                        "trigger_time": str(row.get("trigger_time") or ""),
                        "created_at": str(row.get("created_at") or now_iso()),
                        "platform": str(row.get("platform") or "telegram"),
                    }
                )
            for uid, items in grouped_reminders.items():
                path = user_path(uid, "automation", "reminders.md")
                if not path.exists():
                    _write_json_sync(path, items)
            _set_counter_min_sync("reminder", max_id + 1)
            migrated["tables"].append("reminders")

        # scheduled_tasks -> per user automation/scheduled_tasks.md
        if _table_exists(conn, "scheduled_tasks"):
            rows = _rows(conn, "SELECT * FROM scheduled_tasks")
            max_id = 0
            grouped_tasks: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                tid = int(row.get("id") or 0)
                max_id = max(max_id, tid)
                uid = _safe_part(row.get("user_id") or "0")
                grouped_tasks.setdefault(uid, []).append(
                    {
                        "id": tid,
                        "crontab": str(row.get("crontab") or ""),
                        "instruction": str(row.get("instruction") or ""),
                        "platform": str(row.get("platform") or "telegram"),
                        "need_push": bool(row.get("need_push", 1)),
                        "is_active": bool(row.get("is_active", 1)),
                        "created_at": str(row.get("created_at") or now_iso()),
                        "updated_at": str(row.get("updated_at") or now_iso()),
                    }
                )
            for uid, items in grouped_tasks.items():
                path = user_path(uid, "automation", "scheduled_tasks.md")
                if not path.exists():
                    _write_json_sync(path, items)
            _set_counter_min_sync("scheduled_task", max_id + 1)
            migrated["tables"].append("scheduled_tasks")

    except Exception as exc:
        logger.warning("SQLite migration skipped due to error: %s", exc)
    finally:
        conn.close()

    _write_json_sync(marker, migrated)
    logger.info("Repository store migration completed: %s", migrated.get("tables"))


def _migrate_legacy_file_names() -> None:
    """Convert legacy .json/.jsonl files to .md equivalents once."""
    rename_map = [
        (system_path("allowed_users.json"), system_path("allowed_users.md")),
        (system_path("video_cache.json"), system_path("video_cache.md")),
        (system_path("id_counters.json"), system_path("id_counters.md")),
    ]

    for uid in all_user_ids():
        rename_map.extend(
            [
                (user_path(uid, "settings.json"), user_path(uid, "settings.md")),
                (user_path(uid, "accounts.json"), user_path(uid, "accounts.md")),
                (
                    user_path(uid, "rss", "subscriptions.json"),
                    user_path(uid, "rss", "subscriptions.md"),
                ),
                (
                    user_path(uid, "stock", "watchlist.json"),
                    user_path(uid, "stock", "watchlist.md"),
                ),
            ]
        )

        # Stats feature retired: remove both legacy and markdown stats files.
        for stats_path in (
            user_path(uid, "stats.json"),
            user_path(uid, "stats.md"),
        ):
            if stats_path.exists():
                with contextlib.suppress(Exception):
                    stats_path.unlink()

        old_history = user_path(uid, "chat", "history.jsonl")
        new_history = user_path(uid, "chat", "history.md")
        if old_history.exists() and not new_history.exists():
            rows: list[dict[str, Any]] = []
            try:
                for raw in old_history.read_text(encoding="utf-8").splitlines():
                    text = raw.strip()
                    if not text:
                        continue
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        rows.append(payload)
            except Exception:
                rows = []
            if rows:
                _write_json_sync(new_history, rows)
        if old_history.exists() and new_history.exists():
            with contextlib.suppress(Exception):
                old_history.unlink()

    for old_path, new_path in rename_map:
        if not old_path.exists():
            continue
        if new_path.exists():
            with contextlib.suppress(Exception):
                old_path.unlink()
            continue
        try:
            payload = _read_json_sync(old_path, None)
            if payload is not None:
                _write_json_sync(new_path, payload)
                with contextlib.suppress(Exception):
                    old_path.unlink()
        except Exception:
            continue

    # Migrate old global reminders/tasks files into per-user automation paths.
    for legacy_path in (
        system_path("reminders.json"),
        system_path("reminders.md"),
        system_path("scheduled_tasks.json"),
        system_path("scheduled_tasks.md"),
    ):
        if not legacy_path.exists():
            continue
        payload = _read_json_sync(legacy_path, [])
        if not isinstance(payload, list):
            with contextlib.suppress(Exception):
                legacy_path.unlink()
            continue

        is_reminder = "reminder" in legacy_path.name
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in payload:
            if not isinstance(row, dict):
                continue
            uid = _safe_part(row.get("user_id") or "0")
            if is_reminder:
                grouped.setdefault(uid, []).append(
                    {
                        "id": int(row.get("id") or 0),
                        "chat_id": str(row.get("chat_id") or ""),
                        "message": str(row.get("message") or ""),
                        "trigger_time": str(row.get("trigger_time") or ""),
                        "created_at": str(row.get("created_at") or now_iso()),
                        "platform": str(row.get("platform") or "telegram"),
                    }
                )
            else:
                grouped.setdefault(uid, []).append(
                    {
                        "id": int(row.get("id") or 0),
                        "crontab": str(row.get("crontab") or ""),
                        "instruction": str(row.get("instruction") or ""),
                        "platform": str(row.get("platform") or "telegram"),
                        "need_push": bool(row.get("need_push", True)),
                        "is_active": bool(row.get("is_active", True)),
                        "created_at": str(row.get("created_at") or now_iso()),
                        "updated_at": str(row.get("updated_at") or now_iso()),
                    }
                )

        for uid, items in grouped.items():
            target = (
                user_path(uid, "automation", "reminders.md")
                if is_reminder
                else user_path(uid, "automation", "scheduled_tasks.md")
            )
            existing = _read_json_sync(target, [])
            if not isinstance(existing, list):
                existing = []
            merged = list(existing)
            for item in items:
                iid = int(item.get("id") or 0)
                if iid > 0 and any(
                    int(x.get("id") or 0) == iid for x in merged if isinstance(x, dict)
                ):
                    continue
                merged.append(item)
            _write_json_sync(target, merged)

        with contextlib.suppress(Exception):
            legacy_path.unlink()

    # Compact user-level markdown payloads to minimal schemas.
    for uid in all_user_ids():
        subs_path = user_path(uid, "rss", "subscriptions.md")
        if subs_path.exists():
            rows = _read_json_sync(subs_path, [])
            if isinstance(rows, list):
                compacted = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    feed_url = str(row.get("feed_url") or "").strip()
                    if not feed_url:
                        continue
                    compacted.append(
                        {
                            "feed_url": feed_url,
                            "title": str(row.get("title") or feed_url),
                            "platform": str(row.get("platform") or "telegram"),
                            "last_etag": str(row.get("last_etag") or ""),
                            "last_modified": str(row.get("last_modified") or ""),
                            "last_entry_hash": str(row.get("last_entry_hash") or ""),
                        }
                    )
                _write_json_sync(subs_path, compacted)

        watch_path = user_path(uid, "stock", "watchlist.md")
        if watch_path.exists():
            rows = _read_json_sync(watch_path, [])
            if isinstance(rows, list):
                compacted = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("stock_code") or "").strip()
                    if not code:
                        continue
                    compacted.append(
                        {
                            "stock_code": code,
                            "stock_name": str(row.get("stock_name") or code),
                            "platform": str(row.get("platform") or "telegram"),
                        }
                    )
                _write_json_sync(watch_path, compacted)


async def init_db():
    """Initialize filesystem repository store (legacy name kept)."""
    logger.info("Initializing repository filesystem store under %s", repo_root())

    # Ensure roots exist.
    repo_root()
    users_root()

    # Keep a legacy file placeholder for compatibility tooling.
    db_path = _runtime_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        db_path.touch()

    _migrate_legacy_file_names()

    _migrate_from_sqlite_once()
    logger.info("Repository filesystem store initialized")
