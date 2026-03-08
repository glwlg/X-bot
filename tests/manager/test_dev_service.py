import asyncio
from pathlib import Path

import pytest

import core.platform.registry as registry_module
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
    def __init__(self):
        self.rollout_calls = []

    async def publish(self, **_kwargs):
        return {
            "ok": True,
            "summary": "publish completed",
            "pull_request": {"html_url": "https://github.com/acme/project/pull/9"},
            "commit_sha": "abc123",
        }

    async def rollout_local(self, **kwargs):
        self.rollout_calls.append(dict(kwargs))
        return {
            "ok": True,
            "summary": "local rollout completed for x-bot-worker",
            "target_service": str(kwargs.get("target_service") or ""),
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


class _FakeAdapter:
    def __init__(self):
        self.drafts = []
        self.messages = []

    async def send_message_draft(self, **kwargs):
        self.drafts.append(dict(kwargs))
        return True

    async def send_message(self, **kwargs):
        self.messages.append(dict(kwargs))
        return True


class _FakeAdapterManager:
    def __init__(self, adapter):
        self.adapter = adapter

    def get_adapter(self, platform):
        _ = platform
        return self.adapter


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
async def test_software_delivery_validate_only_stops_after_validation(monkeypatch):
    async def fake_run_coding_backend(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "backend": "codex",
            "summary": "coding backend completed",
        }

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    publisher = _FakePublisher()
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service.workspace = _FakeWorkspace()
    service.planner = _FakePlanner()
    service.validator = _FakeValidator()
    service.publisher = publisher
    service.github = _FakeGitHub()

    result = await service.software_delivery(
        action="run",
        requirement="fix bug",
        repo_path="/tmp/repo",
        backend="codex",
        validate_only=True,
    )

    assert result["ok"] is True
    await _wait_background(service)
    status = await service.software_delivery(action="status", task_id=result["task_id"])
    assert status["status"] == "validated"
    assert publisher.rollout_calls == []


@pytest.mark.asyncio
async def test_software_delivery_publish_runs_local_rollout(monkeypatch):
    async def fake_run_coding_backend(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "backend": "codex",
            "summary": "coding backend completed",
        }

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    publisher = _FakePublisher()
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service.workspace = _FakeWorkspace()
    service.planner = _FakePlanner()
    service.validator = _FakeValidator()
    service.publisher = publisher
    service.github = _FakeGitHub()

    result = await service.software_delivery(
        action="run",
        requirement="fix worker bug",
        repo_path="/tmp/repo",
        backend="codex",
        auto_publish=True,
        auto_push=False,
        auto_pr=False,
        target_service="worker",
        rollout="local",
    )

    assert result["ok"] is True
    await _wait_background(service)
    status = await service.software_delivery(action="status", task_id=result["task_id"])
    assert status["status"] == "done"
    assert publisher.rollout_calls[0]["target_service"] == "worker"
    assert status["data"]["task"]["rollout"]["ok"] is True


@pytest.mark.asyncio
async def test_software_delivery_rollout_failure_marks_task_failed(monkeypatch):
    async def fake_run_coding_backend(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "backend": "codex",
            "summary": "coding backend completed",
        }

    class _FailingPublisher(_FakePublisher):
        async def rollout_local(self, **kwargs):
            self.rollout_calls.append(dict(kwargs))
            return {
                "ok": False,
                "error_code": "rollout_up_failed",
                "message": "service failed to restart",
                "rollback": {"attempted": True, "ok": True},
            }

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    publisher = _FailingPublisher()
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service.workspace = _FakeWorkspace()
    service.planner = _FakePlanner()
    service.validator = _FakeValidator()
    service.publisher = publisher
    service.github = _FakeGitHub()

    result = await service.software_delivery(
        action="run",
        requirement="fix worker bug",
        repo_path="/tmp/repo",
        backend="codex",
        auto_publish=True,
        auto_push=False,
        auto_pr=False,
        target_service="worker",
        rollout="local",
    )

    assert result["ok"] is True
    await _wait_background(service)
    status = await service.software_delivery(action="status", task_id=result["task_id"])
    assert status["status"] == "failed"
    assert status["data"]["task"]["rollout"]["rollback"]["attempted"] is True


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


@pytest.mark.asyncio
async def test_software_delivery_skill_template_respects_contract_block(monkeypatch):
    service = ManagerDevService()
    monkeypatch.setattr(
        service,
        "_resolve_skill_contract",
        lambda **_kwargs: {
            "allow_manager_modify": False,
            "change_level": "worker-kernel",
        },
    )

    result = await service.software_delivery(
        action="skill_modify",
        skill_name="demo_skill",
        instruction="update skill",
        cwd="/tmp/repo/skills/builtin/demo_skill",
    )

    assert result["ok"] is False
    assert result["error_code"] == "skill_contract_blocked"


@pytest.mark.asyncio
async def test_software_delivery_skill_template_runs_contract_preflight(monkeypatch):
    calls = []

    async def fake_run_coding_backend(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "backend": "codex",
            "summary": "template completed",
        }

    async def fake_run_shell(command, *, cwd, timeout_sec=1200):
        calls.append((command, cwd, timeout_sec))
        return {"ok": True, "summary": "ok", "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)
    monkeypatch.setattr("manager.dev.skill_contracts.run_shell", fake_run_shell)

    service = ManagerDevService()
    monkeypatch.setattr(
        service,
        "_resolve_skill_contract",
        lambda **_kwargs: {
            "allow_manager_modify": True,
            "preflight_commands": ["python scripts/execute.py --help"],
        },
    )

    result = await service.software_delivery(
        action="skill_create",
        skill_name="demo_skill",
        instruction="create skill files",
        cwd="/tmp/repo/skills/learned/demo_skill",
    )

    assert result["ok"] is True
    await _wait_background(service)
    assert calls == [
        ("python scripts/execute.py --help", "/tmp/repo/skills/learned/demo_skill", 600)
    ]


@pytest.mark.asyncio
async def test_software_delivery_skill_template_uses_external_source_workspace(
    monkeypatch, tmp_path
):
    source_root = tmp_path / "source-skill"
    (source_root / "scripts").mkdir(parents=True, exist_ok=True)
    (source_root / "SKILL.md").write_text(
        "---\nname: union-search-skill\ndescription: external skill\n---\n",
        encoding="utf-8",
    )
    (source_root / "union_search_cli.py").write_text(
        (
            "import argparse\n"
            "parser = argparse.ArgumentParser()\n"
            "sub = parser.add_subparsers(dest='command')\n"
            "sub.add_parser('doctor')\n"
            "sub.add_parser('list')\n"
            "parser.parse_args()\n"
        ),
        encoding="utf-8",
    )

    async def fake_run_coding_backend(**kwargs):
        raise AssertionError(f"run_coding_backend should not be called: {kwargs}")

    async def fake_run_shell(command, *, cwd, timeout_sec=1200):
        _ = (cwd, timeout_sec)
        safe_command = str(command or "").strip()
        if safe_command == "python union_search_cli.py --help":
            return {
                "ok": True,
                "summary": "ok",
                "stdout": "usage: union_search_cli.py ... {doctor,list}",
                "stderr": "",
            }
        return {
            "ok": False,
            "summary": f"unexpected command: {safe_command}",
            "stdout": "",
            "stderr": f"unexpected command: {safe_command}",
        }

    class _SourceWorkspace(_FakeWorkspace):
        async def prepare_workspace(self, **kwargs):
            if str(kwargs.get("repo_url") or "").strip():
                return {
                    "ok": True,
                    "path": str(source_root),
                    "origin_url": str(kwargs.get("repo_url") or ""),
                    "owner": "runningZ1",
                    "repo": "union-search-skill",
                    "default_branch": "main",
                }
            return await super().prepare_workspace(**kwargs)

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)
    monkeypatch.setattr(service_module, "run_shell", fake_run_shell)
    monkeypatch.setattr("manager.dev.skill_contracts.run_shell", fake_run_shell)

    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service.workspace = _SourceWorkspace()
    service._task_log_path = lambda task_id: str(tmp_path / f"{task_id}.log")
    target_dir = tmp_path / "imported-skill"

    result = await service.software_delivery(
        action="skill_create",
        skill_name="union-search-skill",
        instruction="把这个技能集成给阿黑用",
        cwd=str(target_dir),
        repo_url="https://github.com/runningZ1/union-search-skill",
        backend="codex",
    )

    assert result["ok"] is True
    await _wait_background(service)
    status = await service.software_delivery(action="status", task_id=result["task_id"])
    task = dict(status["data"]["task"] or {})
    implementation = dict(task.get("implementation") or {})
    implementation_result = dict(implementation.get("result") or {})

    assert status["status"] == "done"
    assert implementation.get("backend") == "import"
    assert implementation_result.get("source_root") == str(source_root)
    target_dir = Path(str(task.get("template", {}).get("cwd") or ""))
    assert (target_dir / "SKILL.md").exists()
    assert (target_dir / "union_search_cli.py").exists()
    assert (target_dir / "references" / "upstream_SKILL.md").exists()
    integration = dict(task.get("integration") or {})
    assert integration.get("invoke_command") == "python union_search_cli.py"
    rendered = (target_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "python union_search_cli.py --help" in rendered
    assert "integration_origin: external_skill_import" in rendered


@pytest.mark.asyncio
async def test_software_delivery_skill_template_auto_repairs_imported_skill_when_no_entrypoint(
    monkeypatch, tmp_path
):
    source_root = tmp_path / "source-skill"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text(
        "# Demo Skill\n\nNeeds a compatibility shim.\n",
        encoding="utf-8",
    )

    async def fake_run_coding_backend(**kwargs):
        cwd = Path(str(kwargs.get("cwd") or "")).resolve()
        scripts_dir = cwd / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "execute.py").write_text(
            (
                "import argparse\n"
                "parser = argparse.ArgumentParser(description='shim')\n"
                "parser.parse_args()\n"
            ),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "backend": "codex",
            "summary": "added compatibility shim",
        }

    async def fake_run_shell(command, *, cwd, timeout_sec=1200):
        _ = timeout_sec
        safe_command = str(command or "").strip()
        target = Path(str(cwd or "")).resolve()
        if safe_command == "python scripts/execute.py --help":
            execute_path = target / "scripts" / "execute.py"
            if execute_path.exists():
                return {
                    "ok": True,
                    "summary": "ok",
                    "stdout": "usage: execute.py",
                    "stderr": "",
                }
        return {
            "ok": False,
            "summary": f"command failed: {safe_command}",
            "stdout": "",
            "stderr": f"command failed: {safe_command}",
        }

    class _SourceWorkspace(_FakeWorkspace):
        async def prepare_workspace(self, **kwargs):
            if str(kwargs.get("repo_url") or "").strip():
                return {
                    "ok": True,
                    "path": str(source_root),
                    "origin_url": str(kwargs.get("repo_url") or ""),
                    "owner": "runningZ1",
                    "repo": "demo-skill",
                    "default_branch": "main",
                }
            return await super().prepare_workspace(**kwargs)

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)
    monkeypatch.setattr(service_module, "run_shell", fake_run_shell)
    monkeypatch.setattr("manager.dev.skill_contracts.run_shell", fake_run_shell)

    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service.workspace = _SourceWorkspace()
    service._task_log_path = lambda task_id: str(tmp_path / f"{task_id}.log")
    target_dir = tmp_path / "imported-skill"

    result = await service.software_delivery(
        action="skill_create",
        skill_name="demo-skill",
        instruction="把这个技能集成给阿黑用",
        cwd=str(target_dir),
        repo_url="https://github.com/runningZ1/demo-skill",
        backend="codex",
    )

    assert result["ok"] is True
    await _wait_background(service)
    status = await service.software_delivery(action="status", task_id=result["task_id"])
    task = dict(status["data"]["task"] or {})
    integration = dict(task.get("integration") or {})

    assert status["status"] == "done"
    assert integration.get("repaired") is True
    assert integration.get("invoke_command") == "python scripts/execute.py"
    assert (target_dir / "scripts" / "execute.py").exists()
    assert "python scripts/execute.py --help" in (
        target_dir / "SKILL.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_software_delivery_skill_template_emits_progress_heartbeat_and_compact_completion(
    monkeypatch, tmp_path
):
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        registry_module,
        "adapter_manager",
        _FakeAdapterManager(adapter),
    )
    monkeypatch.setenv("SOFTWARE_DELIVERY_PROGRESS_INTERVAL_SEC", "0.05")

    async def fake_run_coding_backend(**kwargs):
        log_path = str(kwargs.get("log_path") or "").strip()
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        await asyncio.sleep(0.12)
        return {
            "ok": True,
            "backend": "codex",
            "stdout": (
                "能，已经集成到当前目标目录，并做了适配。\n\n"
                "file update:\n"
                "diff --git a/foo b/foo\n"
                "+bar\n"
            ),
            "summary": "diff --git a/foo b/foo",
        }

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service._task_log_path = lambda task_id: str(tmp_path / f"{task_id}.log")

    result = await service.software_delivery(
        action="skill_create",
        skill_name="demo_skill",
        instruction="create skill files",
        cwd=str(tmp_path / "demo_skill"),
        notify_platform="telegram",
        notify_chat_id="123",
        notify_user_id="123",
    )

    assert result["ok"] is True
    await _wait_background(service)

    record = await service.tasks.load(result["task_id"])
    assert isinstance(record, dict)
    assert str(record.get("status") or "") == "done"
    assert dict(record.get("progress") or {}).get("stage") == "skill_template"

    log_path = Path(str(dict(record.get("logs") or {}).get("path") or ""))
    assert log_path.exists()
    assert "heartbeat stage=skill_template" in log_path.read_text(encoding="utf-8")

    assert adapter.drafts
    assert "software_delivery 正在处理" in str(adapter.drafts[-1].get("text") or "")
    assert adapter.messages
    final_text = str(adapter.messages[-1].get("text") or "")
    assert "已经集成到当前目标目录" in final_text
    assert "diff --git" not in final_text


def test_build_completion_message_prefers_concise_backend_summary():
    service = ManagerDevService()
    text = service._build_completion_message(
        {
            "task_id": "dev-compact",
            "status": "done",
            "goal": "skill template execution",
            "implementation": {
                "result": {
                    "stdout": (
                        "能，已经集成到当前目标目录，并做了适配。\n\n"
                        "file update:\n"
                        "diff --git a/foo b/foo\n"
                        "+bar\n"
                    ),
                    "summary": "diff --git a/foo b/foo",
                }
            },
            "events": [
                {
                    "name": "skill_template_done",
                    "detail": "diff --git a/foo b/foo",
                }
            ],
        }
    )

    assert "已经集成到当前目标目录" in text
    assert "diff --git" not in text


@pytest.mark.asyncio
async def test_software_delivery_background_sends_completion_notification(monkeypatch):
    async def fake_run_coding_backend(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "backend": "codex",
            "summary": "coding backend completed",
        }

    class _FakeAdapter:
        def __init__(self):
            self.calls = []

        async def send_message(self, *, chat_id, text, **kwargs):
            self.calls.append(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "kwargs": dict(kwargs),
                }
            )
            return {"ok": True}

    class _FakeAdapterManager:
        def __init__(self, adapter):
            self.adapter = adapter

        def get_adapter(self, platform_name):
            assert platform_name == "telegram"
            return self.adapter

    monkeypatch.setattr(service_module, "run_coding_backend", fake_run_coding_backend)

    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        "core.platform.registry.adapter_manager",
        _FakeAdapterManager(fake_adapter),
    )

    publisher = _FakePublisher()
    service = ManagerDevService()
    service.tasks = _FakeTaskStore()
    service.workspace = _FakeWorkspace()
    service.planner = _FakePlanner()
    service.validator = _FakeValidator()
    service.publisher = publisher
    service.github = _FakeGitHub()

    result = await service.software_delivery(
        action="run",
        requirement="fix worker bug",
        repo_path="/tmp/repo",
        backend="codex",
        validate_only=True,
        notify_platform="telegram",
        notify_chat_id="chat-1",
        notify_user_id="u-1",
    )

    assert result["ok"] is True
    await _wait_background(service)
    assert fake_adapter.calls
    assert fake_adapter.calls[-1]["chat_id"] == "chat-1"
    assert result["task_id"] in fake_adapter.calls[-1]["text"]

    status = await service.software_delivery(action="status", task_id=result["task_id"])
    notify = dict(status["data"]["task"].get("notify") or {})
    assert str(notify.get("completion_sent_at") or "").strip()
