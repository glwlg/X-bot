import pytest

import manager.dev.service as service_module
from manager.dev.service import ManagerDevService


class _FakeTaskStore:
    def __init__(self):
        self.rows = {}
        self.counter = 0

    async def create(self, payload):
        self.counter += 1
        row = dict(payload)
        row["task_id"] = row.get("task_id") or f"dev-{self.counter}"
        self.rows[row["task_id"]] = row
        return dict(row)

    async def load(self, task_id):
        row = self.rows.get(str(task_id))
        return dict(row) if isinstance(row, dict) else None

    async def save(self, payload):
        row = dict(payload)
        self.rows[str(row["task_id"])] = row
        return dict(row)


class _FakeWorkspace:
    async def prepare_workspace(self, **_kwargs):
        return {
            "ok": True,
            "path": "/tmp/repo",
            "origin_url": "https://github.com/acme/project.git",
            "owner": "acme",
            "repo": "project",
            "default_branch": "main",
        }

    async def ensure_branch(self, **_kwargs):
        return {"ok": True, "branch_name": "issue-12-fix-bug", "base_branch": "main"}


class _FakePlanner:
    def build_plan(self, **_kwargs):
        return {
            "goal": "Fix issue behavior",
            "acceptance": ["test passes"],
            "steps": ["implement", "validate", "publish"],
            "branch_name": "issue-12-fix-bug",
            "commit_message": "fix: resolve issue #12",
            "pr_title": "Resolve #12",
            "pr_body": "summary",
        }


class _FakeValidator:
    async def validate(self, **_kwargs):
        return {
            "ok": True,
            "summary": "Validation passed",
            "commands": [{"command": "uv run pytest", "ok": True}],
        }


class _FakePublisher:
    async def publish(self, **_kwargs):
        return {
            "ok": True,
            "summary": "publish completed",
            "pull_request": {"html_url": "https://github.com/acme/project/pull/9"},
            "commit_sha": "abc123",
        }


class _FakeGitHub:
    def __init__(self):
        self.comments = []

    async def fetch_issue(self, issue, *, default_owner="", default_repo=""):
        _ = default_owner
        _ = default_repo
        return {
            "owner": "acme",
            "repo": "project",
            "number": 12,
            "title": f"Issue {issue}",
            "body": "please fix",
            "state": "open",
            "labels": ["bug"],
            "html_url": "https://github.com/acme/project/issues/12",
            "is_pull_request": False,
            "comments": [],
        }

    async def create_issue_comment(self, **kwargs):
        self.comments.append(dict(kwargs))
        return {
            "id": 1,
            "html_url": "https://github.com/acme/project/issues/12#issuecomment-1",
        }


@pytest.mark.asyncio
async def test_software_delivery_run_pipeline_success(monkeypatch):
    async def fake_run_coding_backend(**_kwargs):
        return {
            "ok": True,
            "backend": "codex",
            "summary": "coding backend completed",
            "stdout": "done",
        }

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service.workspace = _FakeWorkspace()
    service.planner = _FakePlanner()
    service.validator = _FakeValidator()
    service.publisher = _FakePublisher()
    service.github = _FakeGitHub()

    result = await service.software_delivery(
        action="run",
        requirement="fix bug",
        issue="acme/project#12",
        repo_path="/tmp/repo",
        backend="codex",
        auto_publish=True,
        auto_push=True,
        auto_pr=True,
    )

    assert result["ok"] is True
    assert result["terminal"] is True
    assert result["task_outcome"] == "done"
    assert result["status"] == "done"
    assert result["task_id"].startswith("dev-")


@pytest.mark.asyncio
async def test_software_delivery_read_issue(monkeypatch):
    service = ManagerDevService()
    service.github = _FakeGitHub()

    result = await service.software_delivery(
        action="read_issue",
        issue="https://github.com/acme/project/issues/7",
    )

    assert result["ok"] is True
    issue_payload = result["data"]["issue"]
    assert issue_payload["number"] == 12
    assert issue_payload["owner"] == "acme"


@pytest.mark.asyncio
async def test_software_delivery_skill_template_action(monkeypatch):
    async def fake_run_coding_backend(**kwargs):
        assert kwargs.get("cwd") == "/tmp/repo/skills/learned/demo_skill"
        assert kwargs.get("instruction") == "create skill files"
        assert kwargs.get("source") == "skill_manager_create"
        return {
            "ok": True,
            "backend": "codex",
            "summary": "template completed",
        }

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    service = ManagerDevService()
    result = await service.software_delivery(
        action="skill_create",
        instruction="create skill files",
        cwd="/tmp/repo/skills/learned/demo_skill",
        backend="codex",
        skill_name="demo_skill",
        source="skill_manager_create",
    )

    assert result["ok"] is True
    assert result["terminal"] is True
    assert result["task_outcome"] == "done"
    data = result["data"]
    assert data["mode"] == "skill_template"
    assert data["template_action"] == "skill_create"


@pytest.mark.asyncio
async def test_software_delivery_skill_template_fills_missing_fields(monkeypatch):
    captured = {}

    async def fake_run_coding_backend(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "backend": "codex",
            "summary": "template completed",
        }

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    service = ManagerDevService()
    result = await service.software_delivery(
        action="skill_create",
        skill_name="demo_skill",
        instruction="",
        cwd="",
    )

    assert result["ok"] is True
    assert captured["instruction"]
    assert str(captured["cwd"]).replace("\\", "/").endswith("skills/learned/demo_skill")
