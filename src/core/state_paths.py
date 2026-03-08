from __future__ import annotations

import importlib
import os
import re
import shutil
from pathlib import Path
from typing import Any

DATA_DIR = importlib.import_module("core.config").DATA_DIR
_PRIVATE_DIR_NAME = "user"
_LEGACY_IMPORT_MARKER = ".legacy-import-complete"


def _runtime_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()


def _configured_admin_ids() -> list[str]:
    try:
        admin_ids = getattr(importlib.import_module("core.config"), "ADMIN_USER_IDS", set())
    except Exception:
        admin_ids = set()
    return [str(item).strip() for item in sorted(admin_ids) if str(item).strip()]


def _legacy_users_root() -> Path:
    return (_runtime_data_dir() / "users").resolve()


def _iter_legacy_user_dirs(preferred_user_id: int | str | None = None) -> list[Path]:
    root = _legacy_users_root()
    if not root.exists():
        return []

    candidates: list[Path] = []
    seen: set[str] = set()

    def add(name: Any) -> None:
        safe = _safe_part(name, fallback="")
        if not safe or safe in seen:
            return
        path = (root / safe).resolve()
        if not path.exists() or not path.is_dir():
            return
        seen.add(safe)
        candidates.append(path)

    add(preferred_user_id)
    for admin_id in _configured_admin_ids():
        add(admin_id)
    return candidates


def _merge_missing_tree(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_dir():
        return
    for child in src.rglob("*"):
        if not child.is_file():
            continue
        relative = child.relative_to(src)
        target = (dst / relative).resolve()
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, target)


def _private_root(preferred_user_id: int | str | None = None) -> Path:
    root = (_runtime_data_dir() / _PRIVATE_DIR_NAME).resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = (root / _LEGACY_IMPORT_MARKER).resolve()
    if marker.exists():
        return root

    for legacy_dir in _iter_legacy_user_dirs(preferred_user_id):
        _merge_missing_tree(legacy_dir, root)

    marker.write_text("ok\n", encoding="utf-8")
    return root


def users_root() -> Path:
    root = _private_root()
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
    path = _private_root(user_id).resolve()
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
    if not root.exists():
        return []
    try:
        has_content = any(item.name != _LEGACY_IMPORT_MARKER for item in root.iterdir())
    except Exception:
        has_content = False
    return [_PRIVATE_DIR_NAME] if has_content else []
