import subprocess
from pathlib import Path

import pytest

from ikaros.dev.session_paths import workspace_state_path
from ikaros.dev.workspace_session_service import WorkspaceSessionService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _init_repo(path: Path) -> Path:
    repo = path / "demo-repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "init",
    )
    return repo


@pytest.mark.asyncio
async def test_prepare_inspect_cleanup_workspace_with_local_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    repo = _init_repo(tmp_path)
    service = WorkspaceSessionService()

    prepared = await service.prepare(
        repo_path=str(repo),
        branch_name="feature/test-workspace",
        base_branch="main",
    )

    assert prepared["ok"] is True
    workspace = dict(prepared["data"] or {})
    workspace_id = str(workspace.get("workspace_id") or "")
    repo_root = Path(str(workspace.get("repo_root") or ""))
    assert workspace_id
    assert repo_root.exists()
    assert workspace_state_path(workspace_id).exists()

    inspected = await service.inspect(workspace_id=workspace_id)
    assert inspected["ok"] is True
    assert inspected["data"]["branch_name"] == "feature/test-workspace"
    assert inspected["data"]["is_dirty"] is False

    (repo_root / "README.md").write_text("changed\n", encoding="utf-8")
    inspected_dirty = await service.inspect(workspace_id=workspace_id)
    assert inspected_dirty["data"]["is_dirty"] is True
    assert "README.md" in inspected_dirty["data"]["dirty_files"]

    cleaned = await service.cleanup(workspace_id=workspace_id)
    assert cleaned["ok"] is True
    assert not repo_root.exists()
    assert not workspace_state_path(workspace_id).exists()


@pytest.mark.asyncio
async def test_prepare_reuses_latest_workspace_when_requested(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    repo = _init_repo(tmp_path)
    service = WorkspaceSessionService()

    first = await service.prepare(
        repo_path=str(repo),
        branch_name="feature/reuse-me",
        base_branch="main",
    )
    assert first["ok"] is True

    reused = await service.prepare(
        repo_path=str(repo),
        branch_name="feature/reuse-me",
        base_branch="main",
        mode="reuse_latest",
    )

    assert reused["ok"] is True
    assert reused["summary"] == "reused existing workspace"
    assert reused["data"]["workspace_id"] == first["data"]["workspace_id"]
