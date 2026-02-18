from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any

DATA_DIR = importlib.import_module("core.config").DATA_DIR


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
