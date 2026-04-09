import subprocess
from pathlib import Path

import pytest

from extension.skills.builtin.git_ops.scripts.service import GitOpsService


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
    repo = path / "git-ops-repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.mark.asyncio
async def test_git_ops_status_reports_dirty_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "feature/status-check")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    service = GitOpsService()
    result = await service.status(repo_root=str(repo), base_branch="main")

    assert result["ok"] is True
    assert result["data"]["branch_name"] == "feature/status-check"
    assert result["data"]["is_dirty"] is True
    assert "README.md" in result["data"]["dirty_files"]


@pytest.mark.asyncio
async def test_git_ops_commit_records_commit_sha(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "feature/commit-check")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    service = GitOpsService()
    result = await service.commit(repo_root=str(repo), message="feat: update readme")

    assert result["ok"] is True
    assert result["data"]["branch_name"] == "feature/commit-check"
    assert result["data"]["commit_sha"]
    assert result["data"]["committed_files"] == ["README.md"]


@pytest.mark.asyncio
async def test_git_ops_push_falls_back_to_fork(monkeypatch):
    service = GitOpsService()

    async def fake_inspect(*, workspace_id="", repo_root=""):
        _ = workspace_id
        return {
            "ok": True,
            "data": {
                "workspace_id": "ws-1",
                "repo_root": repo_root or "/tmp/repo",
                "owner": "Scenx",
                "repo": "fuck-skill",
                "origin_url": "https://github.com/Scenx/fuck-skill.git",
                "base_branch": "main",
                "branch_name": "feature/fork-me",
            },
        }

    async def fake_run_shell(command, *, cwd, timeout_sec=1200):
        _ = (cwd, timeout_sec)
        responses = {
            "git rev-parse --abbrev-ref HEAD": {
                "ok": True,
                "stdout": "feature/fork-me\n",
            },
            "git status --porcelain": {"ok": True, "stdout": ""},
            "git rev-list --count main..HEAD": {"ok": True, "stdout": "1\n"},
            "git push -u origin feature/fork-me": {
                "ok": False,
                "summary": "remote: Permission to Scenx/fuck-skill.git denied to ggg-X-bot.\nfatal: unable to access 'https://github.com/Scenx/fuck-skill.git/': The requested URL returned error: 403",
                "stderr": "remote: Permission to Scenx/fuck-skill.git denied to ggg-X-bot.\nfatal: unable to access 'https://github.com/Scenx/fuck-skill.git/': The requested URL returned error: 403",
            },
        }
        return responses[command]

    async def fake_push_via_fork(**kwargs):
        assert kwargs["upstream_owner"] == "Scenx"
        assert kwargs["repo"] == "fuck-skill"
        assert kwargs["branch_name"] == "feature/fork-me"
        return {
            "ok": True,
            "summary": "pushed to fork",
            "push": {"ok": True, "summary": "pushed to fork"},
            "fork": {
                "owner": "ggg-X-bot",
                "repo": "fuck-skill",
                "remote_name": "fork",
                "remote_url": "https://github.com/ggg-X-bot/fuck-skill.git",
                "head_ref": "ggg-X-bot:feature/fork-me",
            },
        }

    monkeypatch.setattr(
        "extension.skills.builtin.git_ops.scripts.service.workspace_session_service.inspect",
        fake_inspect,
    )
    monkeypatch.setattr(
        "extension.skills.builtin.git_ops.scripts.service.run_shell",
        fake_run_shell,
    )
    monkeypatch.setattr(service.publisher, "_push_via_fork", fake_push_via_fork)

    result = await service.push(repo_root="/tmp/repo", strategy="auto")

    assert result["ok"] is True
    assert result["data"]["remote_name"] == "fork"
    assert result["data"]["head_ref"] == "ggg-X-bot:feature/fork-me"
