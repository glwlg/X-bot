from __future__ import annotations

import asyncio
import copy
import importlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_state_paths = importlib.import_module("core.state_paths")
repo_root = _state_paths.repo_root
system_path = _state_paths.system_path
users_root = _state_paths.users_root
_state_file = importlib.import_module("core.state_file")
parse_state_payload = _state_file.parse_state_payload
render_state_markdown = _state_file.render_state_markdown

logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}
_COUNTERS_FILE = "id_counters.md"


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


def _parse_yaml_payload(text: str) -> tuple[bool, Any]:
    return parse_state_payload(text)


def _read_json_sync(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return copy.deepcopy(default)
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return copy.deepcopy(default)
        ok, loaded = _parse_yaml_payload(text)
        if not ok:
            return copy.deepcopy(default)
        return loaded
    except Exception:
        return copy.deepcopy(default)


def _write_json_sync(path: Path, payload: Any) -> None:
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    title = path.stem.replace("_", " ").strip().title() or "Data"
    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        if existing_text.strip():
            ok, _ = _parse_yaml_payload(existing_text)
            if not ok:
                backup_path = path.with_name(
                    f"{path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                )
                backup_path.write_text(existing_text, encoding="utf-8")

    content = render_state_markdown(payload, title=title)
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
