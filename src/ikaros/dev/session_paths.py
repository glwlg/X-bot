from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from core.app_paths import data_dir


def _system_root() -> Path:
    return (data_dir() / "system").resolve()


def _safe_token(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw in {".", ".."}:
        return fallback
    safe = quote(raw, safe="._-:")
    if not safe or safe in {".", ".."}:
        return fallback
    return safe


def repo_mirror_root(repo_slug: str) -> Path:
    safe_slug = str(repo_slug or "repo").strip() or "repo"
    root = (_system_root() / "dev_repos" / safe_slug).resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def workspace_root(repo_slug: str, workspace_id: str) -> Path:
    safe_slug = str(repo_slug or "repo").strip() or "repo"
    safe_id = str(workspace_id or "workspace").strip() or "workspace"
    root = (_system_root() / "dev_worktrees" / safe_slug / safe_id).resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def workspace_state_path(workspace_id: str) -> Path:
    safe_id = str(workspace_id or "workspace").strip() or "workspace"
    root = (_system_root() / "dev_workspaces").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return (root / f"{safe_id}.json").resolve()


def coding_sessions_root() -> Path:
    return (_system_root() / "coding_sessions").resolve()


def _coding_session_root(session_id: str, *, create: bool) -> Path:
    safe_id = _safe_token(session_id, "session")
    root = (coding_sessions_root() / safe_id).resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def coding_session_root(session_id: str) -> Path:
    return _coding_session_root(session_id, create=False)


def ensure_coding_session_root(session_id: str) -> Path:
    return _coding_session_root(session_id, create=True)


def coding_session_path(session_id: str) -> Path:
    return (coding_session_root(session_id) / "session.json").resolve()


def coding_session_events_path(session_id: str) -> Path:
    return (coding_session_root(session_id) / "events.jsonl").resolve()


def coding_session_log_path(session_id: str) -> Path:
    return (coding_session_root(session_id) / "backend.log").resolve()


def new_workspace_id() -> str:
    return f"ws-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}"


def new_coding_session_id() -> str:
    return f"cs-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}"
