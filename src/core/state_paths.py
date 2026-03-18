from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

DATA_DIR = importlib.import_module("core.config").DATA_DIR
_PRIVATE_DIR_NAME = "user"
SINGLE_USER_SCOPE = "user"


def _runtime_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()


def single_user_root() -> Path:
    root = (_runtime_data_dir() / _PRIVATE_DIR_NAME).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def shared_user_path(*parts: str) -> Path:
    return _append_safe_parts(single_user_root(), parts)


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


def user_path(user_id: int | str, *parts: str) -> Path:
    _ = user_id
    return _append_safe_parts(single_user_root(), parts)


def system_path(*parts: str) -> Path:
    root = (_runtime_data_dir() / "system").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return _append_safe_parts(root, parts)


def all_user_ids() -> list[str]:
    return [SINGLE_USER_SCOPE]
