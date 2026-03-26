import pytest

from ikaros.dev.publisher import IkarosDevPublisher


@pytest.mark.asyncio
async def test_publish_uses_existing_commits_when_worktree_clean(monkeypatch):
    publisher = IkarosDevPublisher()
    commands = []
    pr_calls = []

    async def fake_run_shell(command, *, cwd, timeout_sec=120):
        _ = timeout_sec
        commands.append((command, cwd))
        responses = {
            "git rev-parse --abbrev-ref HEAD": {
                "ok": True,
                "stdout": "feat/nitian-skill\n",
            },
            "git status --porcelain": {"ok": True, "stdout": ""},
            "git rev-list --count main..HEAD": {"ok": True, "stdout": "1\n"},
            "git diff --name-only main..HEAD": {
                "ok": True,
                "stdout": "README.md\nnitian-mode/SKILL.md\n",
            },
            "git rev-parse HEAD": {"ok": True, "stdout": "3097980\n"},
            "git push -u origin feat/nitian-skill": {"ok": True, "summary": "pushed"},
        }
        return responses[command]

    async def fake_create_pull_request(**kwargs):
        pr_calls.append(dict(kwargs))
        return {
            "number": 18,
            "html_url": "https://github.com/acme/project/pull/18",
            "state": "open",
        }

    monkeypatch.setattr("ikaros.dev.publisher.run_shell", fake_run_shell)
    monkeypatch.setattr(
        publisher.github, "create_pull_request", fake_create_pull_request
    )

    result = await publisher.publish(
        repo_path="/tmp/repo",
        owner="acme",
        repo="project",
        branch_name="icarus/add-over-the-top-skill",
        base_branch="main",
        commit_message="feat: add skill",
        pr_title="Add skill",
        pr_body="summary",
        auto_push=True,
        auto_pr=True,
    )

    assert result["ok"] is True
    assert result["branch_name"] == "feat/nitian-skill"
    assert result["base_branch"] == "main"
    assert result["commit_sha"] == "3097980"
    assert result["dirty_worktree"] is False
    assert result["committed_files"] == ["README.md", "nitian-mode/SKILL.md"]
    assert pr_calls[0]["head"] == "feat/nitian-skill"
    assert ("git push -u origin feat/nitian-skill", "/tmp/repo") in commands


@pytest.mark.asyncio
async def test_publish_returns_no_changes_when_clean_and_not_ahead(monkeypatch):
    publisher = IkarosDevPublisher()

    async def fake_run_shell(command, *, cwd, timeout_sec=120):
        _ = (cwd, timeout_sec)
        responses = {
            "git rev-parse --abbrev-ref HEAD": {"ok": True, "stdout": "main\n"},
            "git status --porcelain": {"ok": True, "stdout": ""},
            "git rev-list --count main..HEAD": {"ok": True, "stdout": "0\n"},
        }
        return responses[command]

    monkeypatch.setattr("ikaros.dev.publisher.run_shell", fake_run_shell)

    result = await publisher.publish(
        repo_path="/tmp/repo",
        owner="acme",
        repo="project",
        branch_name="main",
        base_branch="main",
        commit_message="feat: add skill",
        pr_title="Add skill",
        pr_body="summary",
        auto_push=False,
        auto_pr=False,
    )

    assert result["ok"] is False
    assert result["error_code"] == "no_changes"
    assert result["branch_name"] == "main"


@pytest.mark.asyncio
async def test_publish_falls_back_to_fork_when_upstream_push_denied(monkeypatch):
    publisher = IkarosDevPublisher()
    commands = []
    pr_calls = []

    async def fake_run_shell(command, *, cwd, timeout_sec=120):
        _ = timeout_sec
        commands.append((command, cwd))
        responses = {
            "git rev-parse --abbrev-ref HEAD": {
                "ok": True,
                "stdout": "feature/outrageous-skill\n",
            },
            "git status --porcelain": {"ok": True, "stdout": " M README.md\n"},
            "git add -A": {"ok": True, "summary": "added"},
            "git diff --cached --name-only": {"ok": True, "stdout": "README.md\n"},
            "git commit -m 'feat: add skill'": {"ok": True, "summary": "committed"},
            "git rev-parse HEAD": {"ok": True, "stdout": "abc123\n"},
            "git push -u origin feature/outrageous-skill": {
                "ok": False,
                "summary": "remote: Permission to Scenx/fuck-skill.git denied to ggg-X-bot.\nfatal: unable to access 'https://github.com/Scenx/fuck-skill.git/': The requested URL returned error: 403",
                "stderr": "remote: Permission to Scenx/fuck-skill.git denied to ggg-X-bot.\nfatal: unable to access 'https://github.com/Scenx/fuck-skill.git/': The requested URL returned error: 403",
            },
            "git remote get-url fork": {"ok": False, "summary": "no such remote"},
            "git remote add fork https://github.com/ggg-X-bot/fuck-skill.git": {
                "ok": True,
                "summary": "remote added",
            },
            "git push -u fork feature/outrageous-skill": {
                "ok": True,
                "summary": "pushed to fork",
            },
        }
        return responses[command]

    async def fake_ensure_fork(**kwargs):
        assert kwargs == {"owner": "Scenx", "repo": "fuck-skill"}
        return {
            "owner": "ggg-X-bot",
            "repo": "fuck-skill",
            "full_name": "ggg-X-bot/fuck-skill",
            "html_url": "https://github.com/ggg-X-bot/fuck-skill",
            "created": True,
        }

    async def fake_create_pull_request(**kwargs):
        pr_calls.append(dict(kwargs))
        return {
            "number": 19,
            "html_url": "https://github.com/Scenx/fuck-skill/pull/19",
            "state": "open",
        }

    monkeypatch.setattr("ikaros.dev.publisher.run_shell", fake_run_shell)
    monkeypatch.setattr(publisher.github, "ensure_fork", fake_ensure_fork)
    monkeypatch.setattr(
        publisher.github, "create_pull_request", fake_create_pull_request
    )

    result = await publisher.publish(
        repo_path="/tmp/repo",
        owner="Scenx",
        repo="fuck-skill",
        branch_name="feature/outrageous-skill",
        base_branch="main",
        commit_message="feat: add skill",
        pr_title="Add skill",
        pr_body="summary",
        auto_push=True,
        auto_pr=True,
    )

    assert result["ok"] is True
    assert result["fork"]["owner"] == "ggg-X-bot"
    assert result["fork"]["head_ref"] == "ggg-X-bot:feature/outrageous-skill"
    assert pr_calls[0]["head"] == "ggg-X-bot:feature/outrageous-skill"
    assert (
        "git remote add fork https://github.com/ggg-X-bot/fuck-skill.git",
        "/tmp/repo",
    ) in commands
    assert ("git push -u fork feature/outrageous-skill", "/tmp/repo") in commands
