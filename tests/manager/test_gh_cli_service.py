import asyncio
from pathlib import Path

import pytest

from manager.integrations.gh_cli_service import GhCliService, _AuthJob


class _FakeProcess:
    _next_pid = 5000

    def __init__(self, *, stdout_lines=None, stderr_lines=None):
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode = None
        self._waiter = asyncio.get_running_loop().create_future()
        for line in list(stdout_lines or []):
            self.stdout.feed_data((str(line) + "\n").encode("utf-8"))
        for line in list(stderr_lines or []):
            self.stderr.feed_data((str(line) + "\n").encode("utf-8"))

    async def wait(self):
        return await self._waiter

    def finish(self, returncode=0, *, stdout_lines=None, stderr_lines=None):
        if self._waiter.done():
            return
        for line in list(stdout_lines or []):
            self.stdout.feed_data((str(line) + "\n").encode("utf-8"))
        for line in list(stderr_lines or []):
            self.stderr.feed_data((str(line) + "\n").encode("utf-8"))
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = int(returncode)
        self._waiter.set_result(self.returncode)

    def terminate(self):
        self.finish(130)

    def kill(self):
        self.finish(137)


@pytest.mark.asyncio
async def test_auth_start_persists_waiting_session_under_single_user_root(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()
    process = _FakeProcess(
        stdout_lines=[
            "Open this URL to continue in your web browser: https://github.com/login/device",
            "Enter the code: 2807-7039",
        ]
    )

    async def fake_status(hostname: str):
        return {"authenticated": False, "text": "not logged in", "raw": {}}

    async def fake_spawn(argv, *, cwd=None):
        assert argv[:4] == ["gh", "auth", "login", "--web"]
        assert cwd is None
        return process

    monkeypatch.setattr(service, "_auth_status_command", fake_status)
    monkeypatch.setattr(service, "_spawn_process", fake_spawn)

    result = await service.auth_start(
        hostname="github.com",
        notify_platform="telegram",
        notify_chat_id="chat-1",
        notify_user_id="user-1",
    )

    assert result["ok"] is True
    assert "设备码" in result["text"]
    assert "2807-7039" in result["text"]
    assert (
        str((tmp_path / "user" / "integrations" / "gh" / "config").resolve())
        in result["text"]
    )

    session = await service._read_session("github.com")
    assert session["status"] == "waiting_user"
    assert session["user_code"] == "2807-7039"
    assert session["verification_uri"] == "https://github.com/login/device"
    assert Path(service._session_path("github.com")).is_file()

    cancel = await service.auth_cancel(hostname="github.com")
    assert cancel["ok"] is True


@pytest.mark.asyncio
async def test_auth_background_completion_updates_session_and_notifies(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()
    process = _FakeProcess(
        stdout_lines=[
            "Open this URL to continue in your web browser: https://github.com/login/device",
            "Enter the code: 2807-7039",
        ]
    )
    sent_messages = []
    auth_status_calls = {"count": 0}

    async def fake_status(hostname: str):
        auth_status_calls["count"] += 1
        if auth_status_calls["count"] == 1:
            return {"authenticated": False, "text": "not logged in", "raw": {}}
        return {
            "authenticated": True,
            "text": "Logged in to github.com as octocat",
            "raw": {"ok": True},
        }

    async def fake_spawn(argv, *, cwd=None):
        assert cwd is None
        return process

    async def fake_run_capture(argv, *, cwd=None, timeout_sec=120):
        assert argv == ["gh", "auth", "setup-git"]
        return {
            "ok": True,
            "exit_code": 0,
            "output": "git credential helper configured",
        }

    async def fake_push_background_text(**kwargs):
        sent_messages.append(dict(kwargs))
        return True

    monkeypatch.setattr(service, "_auth_status_command", fake_status)
    monkeypatch.setattr(service, "_spawn_process", fake_spawn)
    monkeypatch.setattr(service, "_run_capture", fake_run_capture)
    monkeypatch.setattr(
        "manager.integrations.gh_cli_service.push_background_text",
        fake_push_background_text,
    )

    result = await service.auth_start(
        hostname="github.com",
        notify_platform="telegram",
        notify_chat_id="chat-1",
        notify_user_id="user-1",
    )
    assert result["ok"] is True
    assert "设备码" in result["text"]

    job = service._job_for("github.com")
    assert job is not None
    process.finish(0)
    await asyncio.wait_for(job.task, timeout=2)

    session = await service._read_session("github.com")
    assert session["status"] == "authenticated"
    assert sent_messages
    assert "GitHub 登录已完成" in str(sent_messages[0]["text"])
    assert sent_messages[0]["record_history"] is True
    assert sent_messages[0]["history_user_id"] == "user-1"


@pytest.mark.asyncio
async def test_auth_status_reports_persisted_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()

    async def fake_status(hostname: str):
        return {
            "authenticated": True,
            "text": "Logged in to github.com as octocat",
            "raw": {"ok": True},
        }

    monkeypatch.setattr(service, "_auth_status_command", fake_status)

    result = await service.auth_status(hostname="github.com")

    assert result["ok"] is True
    assert result["terminal"] is False
    assert result["text"] == ""
    assert result["task_outcome"] == ""
    assert result["history_visibility"] == "suppress_success"
    assert "octocat" in result["data"]["auth_status"]["text"]
    session = await service._read_session("github.com")
    assert session["status"] == "authenticated"


@pytest.mark.asyncio
async def test_auth_status_waiting_probe_is_non_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()
    process = _FakeProcess()
    job_task = asyncio.create_task(asyncio.sleep(60))

    await service._write_session(
        "github.com",
        {
            "status": "waiting_user",
            "hostname": "github.com",
            "verification_uri": "https://github.com/login/device",
            "user_code": "2807-7039",
        },
    )
    service._auth_jobs["github.com"] = _AuthJob(
        hostname="github.com",
        process=process,
        task=job_task,
        ready_event=asyncio.Event(),
    )
    monkeypatch.setattr(service, "_process_alive", lambda _pid: True)

    try:
        result = await service.auth_status(hostname="github.com")
    finally:
        job_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job_task

    assert result["ok"] is True
    assert result["terminal"] is False
    assert result["task_outcome"] == ""
    assert result["summary"] == "auth still waiting for github.com"


@pytest.mark.asyncio
async def test_auth_status_interrupted_probe_is_non_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()

    await service._write_session(
        "github.com",
        {
            "status": "waiting_user",
            "hostname": "github.com",
            "verification_uri": "https://github.com/login/device",
            "user_code": "2807-7039",
        },
    )

    result = await service.auth_status(hostname="github.com")

    assert result["ok"] is True
    assert result["terminal"] is False
    assert result["task_outcome"] == ""
    assert result["summary"] == "auth interrupted for github.com"
    session = await service._read_session("github.com")
    assert session["status"] == "interrupted"


@pytest.mark.asyncio
async def test_exec_blocks_interactive_auth_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()

    result = await service.exec(argv=["auth", "login"])

    assert result["ok"] is False
    assert result["error_code"] == "interactive_command_blocked"


@pytest.mark.asyncio
async def test_exec_success_is_non_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()

    async def fake_run_capture(argv, *, cwd=None, timeout_sec=120):
        assert argv == [
            "gh",
            "pr",
            "list",
            "--repo",
            "Scenx/fuck-skill",
            "--json",
            "number",
        ]
        assert cwd is None
        assert timeout_sec == 30
        return {
            "ok": True,
            "exit_code": 0,
            "output": "[]",
            "stdout": "[]",
            "stderr": "",
        }

    monkeypatch.setattr(service, "_run_capture", fake_run_capture)

    result = await service.exec(
        argv=["pr", "list", "--repo", "Scenx/fuck-skill", "--json", "number"],
        timeout_sec=30,
    )

    assert result["ok"] is True
    assert result["terminal"] is False
    assert result["task_outcome"] == ""
    assert result["text"] == "[]"


@pytest.mark.asyncio
async def test_exec_auth_failure_is_non_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service = GhCliService()

    async def fake_run_capture(argv, *, cwd=None, timeout_sec=120):
        assert argv == ["gh", "pr", "create", "--repo", "Scenx/fuck-skill"]
        assert cwd is None
        assert timeout_sec == 30
        return {
            "ok": False,
            "exit_code": 4,
            "output": "authentication required; try authenticating with: gh auth login",
            "stdout": "",
            "stderr": "authentication required",
        }

    monkeypatch.setattr(service, "_run_capture", fake_run_capture)

    result = await service.exec(
        argv=["pr", "create", "--repo", "Scenx/fuck-skill"],
        timeout_sec=30,
    )

    assert result["ok"] is False
    assert result["terminal"] is False
    assert result["task_outcome"] == ""
    assert result["error_code"] == "not_authenticated"
