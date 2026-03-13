from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from core.config import DATA_DIR


def _system_root() -> Path:
    configured = str(os.getenv("DATA_DIR", DATA_DIR) or DATA_DIR).strip()
    return (Path(configured).resolve() / "system").resolve()


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


def codex_session_path(session_id: str) -> Path:
    safe_id = str(session_id or "session").strip() or "session"
    root = (_system_root() / "codex_sessions").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return (root / f"{safe_id}.json").resolve()


def codex_session_log_path(session_id: str) -> Path:
    safe_id = str(session_id or "session").strip() or "session"
    root = (_system_root() / "codex_sessions" / "logs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return (root / f"{safe_id}.log").resolve()


def new_workspace_id() -> str:
    return f"ws-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}"


def new_codex_session_id() -> str:
    return f"cx-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}"
