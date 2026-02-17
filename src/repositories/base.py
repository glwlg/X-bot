"""Filesystem repository primitives."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.config import DATA_DIR

logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}
_COUNTERS_FILE = "id_counters.md"


def _runtime_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()


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


async def init_db() -> None:
    logger.info("Initializing repository filesystem store under %s", repo_root())
    repo_root()
    users_root()
    logger.info("Repository filesystem store initialized")
