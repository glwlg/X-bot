from pathlib import Path
from types import SimpleNamespace

import pytest

import core.agent_orchestrator as orchestrator_module
import core.worker_runtime as worker_runtime_module
from core.worker_runtime import WorkerRuntime


class _FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


class _FakeRegistry:
    def __init__(self, workspace_root: str):
        self.worker = {
            "id": "worker-main",
            "backend": "codex",
            "workspace_root": workspace_root,
            "status": "ready",
        }
        self.updated = []

    async def get_worker(self, worker_id: str):
        if worker_id == "worker-main":
            return dict(self.worker)
        return None

    async def update_worker(self, worker_id: str, **fields):
        if worker_id != "worker-main":
            return None
        self.worker.update(fields)
        self.updated.append((worker_id, dict(fields)))
        return dict(self.worker)


class _FakeTaskStore:
    def __init__(self):
        self.created = []
        self.updated = []

    async def create_task(self, **kwargs):
        self.created.append(dict(kwargs))
        return {
            "task_id": "wt-1",
            "created_at": "2026-02-15T00:00:00+00:00",
            **kwargs,
        }

    async def update_task(self, task_id: str, **fields):
        row = {"task_id": task_id, **fields}
        self.updated.append(row)
        return row


def _allow_all_backends(monkeypatch):
    monkeypatch.setattr(
        worker_runtime_module.tool_access_store,
        "is_backend_allowed",
        lambda **_kwargs: (True, {"reason": "test_override"}),
    )


@pytest.mark.asyncio
async def test_worker_runtime_execute_task_local_mode(monkeypatch, tmp_path):
    _allow_all_backends(monkeypatch)
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "local"
    captured = {}

    async def _spawn_local(cmd: str, args: list[str], workspace: Path):
        captured["cmd"] = cmd
        captured["args"] = list(args)
        captured["workspace"] = str(workspace)
        return {
            "ok": True,
            "process": _FakeProcess(returncode=0, stdout=b"done", stderr=b""),
        }

    async def _spawn_docker(cmd: str, args: list[str], workspace: Path):
        raise AssertionError("docker spawn should not be used in local mode")

    monkeypatch.setattr(runtime, "_spawn_local_process", _spawn_local)
    monkeypatch.setattr(runtime, "_spawn_docker_process", _spawn_docker)

    result = await runtime.execute_task(
        worker_id="worker-main",
        source="user",
        instruction="hello world",
        backend="codex",
    )

    assert result["ok"] is True
    assert result["runtime_mode"] == "local"
    assert "done" in result["summary"]
    assert captured["cmd"] == runtime.codex_cmd
    assert captured["workspace"].endswith("workers/worker-main")
    assert any(row.get("status") == "running" for row in fake_store.updated)
    assert any(row.get("status") == "done" for row in fake_store.updated)
    done_rows = [row for row in fake_store.updated if row.get("status") == "done"]
    assert done_rows
    assert "done" in str(done_rows[-1].get("output", {}).get("text") or "")


@pytest.mark.asyncio
async def test_worker_runtime_execute_task_docker_prepare_failure(
    monkeypatch, tmp_path
):
    _allow_all_backends(monkeypatch)
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "docker"
    runtime.docker_data_dir = str(tmp_path / "docker-data")
    runtime.fallback_to_core_agent = False
    captured = {}

    async def _spawn_docker(cmd: str, args: list[str], workspace: Path):
        captured["workspace"] = str(workspace)
        return {"ok": False, "error": "worker container `x-bot-worker` is not running"}

    monkeypatch.setattr(runtime, "_spawn_docker_process", _spawn_docker)

    result = await runtime.execute_task(
        worker_id="worker-main",
        source="heartbeat",
        instruction="fix issue",
        backend="codex",
    )

    assert result["ok"] is False
    assert result["error"] == "exec_prepare_failed"
    assert result["runtime_mode"] == "docker"
    assert "not running" in result["summary"]
    assert captured["workspace"] == str(
        Path(runtime.docker_data_dir) / "userland" / "workers" / "worker-main"
    )


@pytest.mark.asyncio
async def test_worker_runtime_build_auth_start_command(monkeypatch, tmp_path):
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "docker"
    runtime.docker_container = "x-bot-worker"
    runtime.docker_data_dir = str(tmp_path / "docker-data")

    result = await runtime.build_auth_start_command("worker-main", "codex")

    assert result["ok"] is True
    assert result["provider"] == "codex"
    assert "docker exec -it x-bot-worker" in result["command"]
    assert "auth login" in result["command"]


@pytest.mark.asyncio
async def test_worker_runtime_check_auth_status_authenticated(monkeypatch, tmp_path):
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "docker"
    runtime.docker_data_dir = str(tmp_path / "docker-data")

    async def _execute_command(**kwargs):
        return {
            "ok": True,
            "error": "",
            "message": "",
            "exit_code": 0,
            "stdout": b"Logged in as demo",
            "stderr": b"",
        }

    monkeypatch.setattr(runtime, "_execute_command", _execute_command)
    status = await runtime.check_auth_status("worker-main", "codex")

    assert status["ok"] is True
    assert status["authenticated"] is True
    assert status["status"] == "authenticated"


@pytest.mark.asyncio
async def test_worker_runtime_shell_backend_builds_sh_lc(monkeypatch, tmp_path):
    _allow_all_backends(monkeypatch)
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "local"
    runtime.shell_cmd = "sh"
    captured = {}

    async def _spawn_local(cmd: str, args: list[str], workspace: Path):
        captured["cmd"] = cmd
        captured["args"] = list(args)
        return {
            "ok": True,
            "process": _FakeProcess(returncode=0, stdout=b"hello", stderr=b""),
        }

    monkeypatch.setattr(runtime, "_spawn_local_process", _spawn_local)

    result = await runtime.execute_task(
        worker_id="worker-main",
        source="user",
        instruction="echo hello",
        backend="shell",
    )

    assert result["ok"] is True
    assert result["backend"] == "shell"
    assert captured["cmd"] == "sh"
    assert captured["args"] == ["-lc", "echo hello"]


@pytest.mark.asyncio
async def test_worker_runtime_prepare_failed_falls_back_to_core_agent(
    monkeypatch, tmp_path
):
    _allow_all_backends(monkeypatch)
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "docker"
    runtime.fallback_to_core_agent = True
    runtime.docker_data_dir = str(tmp_path / "docker-data")

    async def _execute_command(**kwargs):
        return {
            "ok": False,
            "error": "prepare_failed",
            "message": "CLI `codex` is unavailable in `x-bot-worker`.",
            "exit_code": -1,
            "stdout": b"",
            "stderr": b"",
        }

    async def _core_agent(**kwargs):
        return {
            "ok": True,
            "summary": "handled by core-agent",
            "result": "final answer",
            "text": "final answer",
            "ui": {"actions": [[{"text": "刷新", "callback_data": "rss_refresh"}]]},
            "payload": {
                "text": "final answer",
                "ui": {"actions": [[{"text": "刷新", "callback_data": "rss_refresh"}]]},
            },
            "error": "",
        }

    monkeypatch.setattr(runtime, "_execute_command", _execute_command)
    monkeypatch.setattr(runtime, "_execute_core_agent_task", _core_agent)

    result = await runtime.execute_task(
        worker_id="worker-main",
        source="user_chat",
        instruction="deep research openai codex",
        backend="codex",
        metadata={"user_id": "u1"},
    )

    assert result["ok"] is True
    assert result["backend"] == "core-agent"
    assert result["fallback_from_backend"] == "codex"
    assert result["text"] == "final answer"
    assert result["ui"]["actions"][0][0]["text"] == "刷新"
    done_rows = [row for row in fake_store.updated if row.get("status") == "done"]
    assert done_rows
    assert done_rows[-1].get("output", {}).get("ui", {}).get("actions")


@pytest.mark.asyncio
async def test_worker_runtime_keeps_explicit_backend_for_simple_chat_command(
    monkeypatch, tmp_path
):
    _allow_all_backends(monkeypatch)
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "local"
    runtime.shell_cmd = "sh"
    captured = {}

    async def _execute_command(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "error": "",
            "message": "",
            "exit_code": 0,
            "stdout": b"ok",
            "stderr": b"",
        }

    monkeypatch.setattr(runtime, "_execute_command", _execute_command)

    result = await runtime.execute_task(
        worker_id="worker-main",
        source="user_chat",
        instruction="echo hello",
        backend="codex",
    )

    assert result["ok"] is True
    assert result["backend"] == "codex"
    assert captured["cmd"] == runtime.codex_cmd


@pytest.mark.asyncio
async def test_worker_runtime_blocks_backend_by_policy(monkeypatch, tmp_path):
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)
    monkeypatch.setattr(
        worker_runtime_module.tool_access_store,
        "is_backend_allowed",
        lambda **_kwargs: (False, {"reason": "matched_deny_list"}),
    )

    runtime = WorkerRuntime()
    runtime.runtime_mode = "local"
    result = await runtime.execute_task(
        worker_id="worker-main",
        source="user_cmd",
        instruction="build this",
        backend="codex",
    )
    assert result["ok"] is False
    assert result["error"] == "policy_blocked"
    assert any(row.get("status") == "running" for row in fake_store.updated)
    assert any(row.get("status") == "failed" for row in fake_store.updated)


@pytest.mark.asyncio
async def test_worker_runtime_falls_back_to_allowed_backend(monkeypatch, tmp_path):
    fake_registry = _FakeRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)

    def _backend_policy(**kwargs):
        backend = str(kwargs.get("backend") or "")
        if backend == "core-agent":
            return True, {"reason": "allowed"}
        return False, {"reason": "matched_deny_list"}

    monkeypatch.setattr(
        worker_runtime_module.tool_access_store,
        "is_backend_allowed",
        _backend_policy,
    )

    runtime = WorkerRuntime()
    runtime.runtime_mode = "local"

    async def _core_agent(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "summary": "handled by core-agent",
            "result": "final answer",
            "error": "",
        }

    monkeypatch.setattr(runtime, "_execute_core_agent_task", _core_agent)

    result = await runtime.execute_task(
        worker_id="worker-main",
        source="user_cmd",
        instruction="build this",
        backend="gemini-cli",
    )

    assert result["ok"] is True
    assert result["backend"] == "core-agent"
    assert any(row.get("status") == "done" for row in fake_store.updated)


@pytest.mark.asyncio
async def test_worker_core_agent_context_keeps_logical_user_id(monkeypatch):
    runtime = WorkerRuntime()
    captured = {}

    async def fake_handle_message(ctx, message_history):
        _ = message_history
        captured["user_id"] = str(ctx.message.user.id)
        captured["runtime_user_id"] = str(ctx.user_data.get("runtime_user_id") or "")
        captured["platform"] = str(ctx.message.platform)
        yield "ok"

    fake_orchestrator = SimpleNamespace(handle_message=fake_handle_message)
    monkeypatch.setattr(orchestrator_module, "agent_orchestrator", fake_orchestrator)

    result = await runtime._execute_core_agent_task(
        worker_id="worker-main",
        instruction="检查订阅",
        metadata={"user_id": "42"},
    )

    assert result["ok"] is True
    assert captured["user_id"] == "42"
    assert captured["runtime_user_id"] == "worker::worker-main::42"
    assert captured["platform"] == "worker_runtime"


@pytest.mark.asyncio
async def test_worker_core_agent_collects_pending_files(monkeypatch, tmp_path):
    runtime = WorkerRuntime()

    async def fake_handle_message(ctx, message_history):
        _ = message_history
        await ctx.reply_document(document=b"fake-image", filename="dog.png")
        yield "图片已生成"

    fake_orchestrator = SimpleNamespace(handle_message=fake_handle_message)
    monkeypatch.setattr(orchestrator_module, "agent_orchestrator", fake_orchestrator)

    result = await runtime._execute_core_agent_task(
        worker_id="worker-main",
        instruction="画一只狗",
        metadata={"user_id": "42"},
        workspace_root=str(tmp_path),
    )

    assert result["ok"] is True
    payload = result.get("payload") or {}
    files = payload.get("files") or []
    assert files
    first = files[0]
    assert first["filename"] == "dog.png"
    assert Path(first["path"]).exists()


@pytest.mark.asyncio
async def test_worker_core_agent_deduplicates_same_filename_files(
    monkeypatch, tmp_path
):
    runtime = WorkerRuntime()

    async def fake_handle_message(ctx, message_history):
        _ = message_history
        await ctx.reply_document(document=b"v1", filename="search_report.html")
        await ctx.reply_document(document=b"v2", filename="search_report.html")
        yield "完成"

    fake_orchestrator = SimpleNamespace(handle_message=fake_handle_message)
    monkeypatch.setattr(orchestrator_module, "agent_orchestrator", fake_orchestrator)

    result = await runtime._execute_core_agent_task(
        worker_id="worker-main",
        instruction="查询天气",
        metadata={"user_id": "42"},
        workspace_root=str(tmp_path),
    )

    assert result["ok"] is True
    payload = result.get("payload") or {}
    files = payload.get("files") or []
    assert len(files) == 1
    assert files[0]["filename"] == "search_report.html"
    saved = Path(files[0]["path"])
    assert saved.exists()
    assert saved.read_bytes() == b"v2"
