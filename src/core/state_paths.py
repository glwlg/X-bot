from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

DATA_DIR = importlib.import_module("core.config").DATA_DIR
_PRIVATE_DIR_NAME = "user"
_LOGICAL_USER_IDS_FILE = ".logical_user_ids.json"


def _runtime_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()


def single_user_root() -> Path:
    root = (_runtime_data_dir() / _PRIVATE_DIR_NAME).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def shared_user_path(*parts: str) -> Path:
    return _append_safe_parts(single_user_root(), parts)


def users_root() -> Path:
    root = (_runtime_data_dir() / "users").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def repo_root() -> Path:
    root = system_path("repositories")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_part(value: Any, fallback: str = "unknown") -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if raw in {".", ".."}:
        return fallback
    safe = quote(raw, safe="._-:")
    if safe in {".", ".."}:
        return fallback
    return safe or fallback


def _append_safe_parts(base: Path, parts: tuple[str, ...]) -> Path:
    path = base.resolve()
    for part in parts:
        path = (path / _safe_part(part)).resolve()
    return path


def _logical_user_id_from_dir(name: str) -> str:
    return unquote(str(name or "")).strip()


def _logical_user_ids_path() -> Path:
    return (single_user_root() / _LOGICAL_USER_IDS_FILE).resolve()


def _load_registered_user_ids() -> list[str]:
    path = _logical_user_ids_path()
    try:
        if not path.exists():
            return []
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(loaded, list):
        return []
    rows: list[str] = []
    for item in loaded:
        token = str(item or "").strip()
        if token and token not in rows:
            rows.append(token)
    return rows


def _remember_logical_user_id(user_id: int | str) -> None:
    token = str(user_id or "").strip()
    if not token or token == "private":
        return
    path = _logical_user_ids_path()
    rows = _load_registered_user_ids()
    if token in rows:
        return
    rows.append(token)
    try:
        path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        return


def user_path(user_id: int | str, *parts: str) -> Path:
    _remember_logical_user_id(user_id)
    return _append_safe_parts(single_user_root(), parts)


def system_path(*parts: str) -> Path:
    root = (_runtime_data_dir() / "system").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return _append_safe_parts(root, parts)


def all_user_ids() -> list[str]:
    rows = _load_registered_user_ids()
    root = users_root()
    if root.exists():
        for item in root.iterdir():
            if not item.is_dir() or item.name == "":
                continue
            logical_user_id = _logical_user_id_from_dir(item.name)
            if not logical_user_id or logical_user_id == "private":
                continue
            if logical_user_id not in rows:
                rows.append(logical_user_id)
    return sorted(rows)
