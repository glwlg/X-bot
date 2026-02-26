import asyncio
from pathlib import Path

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

    async def list_recent(self, limit=20):
        _ = limit
        values = list(self.rows.values())
        values.reverse()
        return [dict(item) for item in values]


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


async def _wait_background(service: ManagerDevService) -> None:
    jobs = list(service._background_jobs.values())
    if not jobs:
        return
    await asyncio.gather(*jobs)


@pytest.mark.asyncio
async def test_software_delivery_run_pipeline_success(monkeypatch):
    async def fake_run_coding_backend(**kwargs):
        log_path = str(kwargs.get("log_path") or "").strip()
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text("backend log output\n", encoding="utf-8")
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
    assert result["async_dispatch"] is True
    assert result["status"] == "queued"
    assert result["task_id"].startswith("dev-")

    await _wait_background(service)
    status = await service.software_delivery(action="status", task_id=result["task_id"])
    assert status["ok"] is True
    assert status["status"] == "done"
    assert status["data"]["log_path"].endswith(f"{result['task_id']}.log")
    assert "backend log output" in status["data"]["log_tail"]


@pytest.mark.asyncio
async def test_software_delivery_logs_action_returns_log_tail(tmp_path):
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()

    log_path = tmp_path / "dev-logs.log"
    log_path.write_text("codex step 1\ncodex step 2\n", encoding="utf-8")
    await service.tasks.create(
        {
            "task_id": "dev-logs",
            "status": "implementing",
            "goal": "debug logs",
            "logs": {"path": str(log_path)},
        }
    )

    result = await service.software_delivery(action="logs", task_id="dev-logs")

    assert result["ok"] is True
    assert result["summary"] == "task logs"
    assert result["task_id"] == "dev-logs"
    assert result["status"] == "implementing"
    assert "codex step 2" in result["text"]
    assert result["data"]["log_path"] == str(log_path)


@pytest.mark.asyncio
async def test_software_delivery_logs_action_without_log_file_returns_placeholder():
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()

    await service.tasks.create(
        {
            "task_id": "dev-empty-log",
            "status": "queued",
            "goal": "debug logs",
        }
    )

    result = await service.software_delivery(action="logs", task_id="dev-empty-log")

    assert result["ok"] is True
    assert result["summary"] == "task logs"
    assert result["text"] == "no logs available"


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
async def test_software_delivery_status_uses_latest_when_task_id_missing():
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()

    created = await service.tasks.create(
        {
            "task_id": "dev-42",
            "status": "implemented",
            "goal": "Fix postal code skill",
        }
    )

    result = await service.software_delivery(action="status", task_id="")

    assert result["ok"] is True
    assert result["task_id"] == created["task_id"]
    assert result["status"] == "implemented"


@pytest.mark.asyncio
async def test_software_delivery_status_without_tasks_returns_idle():
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()

    result = await service.software_delivery(action="status", task_id="")

    assert result["ok"] is True
    assert result["status"] == "idle"
    assert result["summary"] == "no software_delivery task found"


@pytest.mark.asyncio
async def test_software_delivery_status_with_unknown_task_id_uses_latest():
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()

    created = await service.tasks.create(
        {
            "task_id": "dev-42",
            "status": "implemented",
            "goal": "Fix postal code skill",
        }
    )

    result = await service.software_delivery(action="status", task_id="dev-missing")

    assert result["ok"] is True
    assert result["task_id"] == created["task_id"]
    assert result["status"] == "implemented"
    assert result["data"]["requested_task_id"] == "dev-missing"
    assert result["data"]["fallback_to_latest"] is True


@pytest.mark.asyncio
async def test_software_delivery_status_with_unknown_task_id_and_no_tasks_is_idle():
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()

    result = await service.software_delivery(action="status", task_id="dev-missing")

    assert result["ok"] is True
    assert result["status"] == "idle"
    assert result["summary"] == "no software_delivery task found"


def test_response_auto_terminal_for_fatal_failure():
    service = ManagerDevService()
    result = service._response(
        ok=False,
        summary="workspace is not a git repository",
        error_code="workspace_failed",
    )
    assert result["ok"] is False
    assert result["terminal"] is True
    assert result["task_outcome"] == "failed"
    assert result["failure_mode"] == "fatal"


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
    assert result["async_dispatch"] is True
    assert result["status"] == "queued"

    await _wait_background(service)
    status = await service.software_delivery(action="status", task_id=result["task_id"])
    assert status["ok"] is True
    assert status["status"] == "done"


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
    await _wait_background(service)
    assert captured["instruction"]
    assert str(captured["cwd"]).replace("\\", "/").endswith("skills/learned/demo_skill")
