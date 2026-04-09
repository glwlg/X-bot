from pathlib import Path

import pytest

from extension.skills.builtin.coding_session.scripts.service import (
    CodingSessionService,
)


@pytest.mark.asyncio
async def test_coding_session_start_waits_for_user_when_backend_asks(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def fake_run_coding_backend(**kwargs):
        assert kwargs["cwd"] == str(workspace_dir)
        return {
            "ok": True,
            "backend": "codex",
            "summary": "请你选择：\n1. 保留这行\n2. 移除这行",
            "stdout": "请你选择：\n1. 保留这行\n2. 移除这行",
        }

    monkeypatch.setattr(
        "extension.skills.builtin.coding_session.scripts.service.run_coding_backend",
        fake_run_coding_backend,
    )

    service = CodingSessionService()
    result = await service.start(
        cwd=str(workspace_dir),
        instruction="inspect README and implement a new skill",
        backend="codex",
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "waiting_user"
    assert "请你选择" in result["data"]["question"]
    assert result["data"]["session_id"]


@pytest.mark.asyncio
async def test_coding_session_continue_injects_question_and_user_reply(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    captured = {"instructions": []}

    async def fake_run_shell(command, *, cwd, timeout_sec=1200):
        _ = timeout_sec
        if command == "git status --short --branch":
            return {"ok": True, "stdout": "## feature/demo\n M README.md\n"}
        if command == "git rev-parse --abbrev-ref HEAD":
            return {"ok": True, "stdout": "feature/demo\n"}
        raise AssertionError(command)

    async def fake_run_coding_backend(**kwargs):
        captured["instructions"].append(kwargs["instruction"])
        return {
            "ok": True,
            "backend": "codex",
            "summary": "done",
            "stdout": "implemented",
        }

    monkeypatch.setattr(
        "extension.skills.builtin.coding_session.scripts.service.run_shell",
        fake_run_shell,
    )
    monkeypatch.setattr(
        "extension.skills.builtin.coding_session.scripts.service.run_coding_backend",
        fake_run_coding_backend,
    )

    service = CodingSessionService()
    created = await service._save_state(
        {
            "session_id": "cs-test",
            "workspace_id": "",
            "repo_root": str(workspace_dir),
            "backend": "codex",
            "instruction": "base instruction",
            "status": "waiting_user",
            "summary": "waiting",
            "pending_question": "请你选择：1. 保留这行 2. 移除这行",
            "result": {},
            "history": [],
            "log_path": str(tmp_path / "data" / "log.txt"),
            "created_at": "2026-03-13T00:00:00+08:00",
        }
    )
    assert created["session_id"] == "cs-test"

    continued = await service.continue_session(
        session_id="cs-test",
        user_reply="移除这行，只保留本次改动",
    )

    assert continued["ok"] is True
    assert continued["data"]["status"] == "done"
    assert captured["instructions"]
    assert "Previous blocking question" in captured["instructions"][0]
    assert "User reply / decision" in captured["instructions"][0]
    assert "移除这行" in captured["instructions"][0]


@pytest.mark.asyncio
async def test_coding_session_acp_continue_reuses_transport_session(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    captured = {"calls": []}

    async def fake_run_coding_backend(**kwargs):
        captured["calls"].append(kwargs)
        return {
            "ok": True,
            "backend": "opencode",
            "transport": "acp",
            "transport_session_id": "acp-session-2",
            "summary": "done",
            "stdout": "implemented",
        }

    monkeypatch.setattr(
        "extension.skills.builtin.coding_session.scripts.service.run_coding_backend",
        fake_run_coding_backend,
    )

    service = CodingSessionService()
    created = await service._save_state(
        {
            "session_id": "cs-acp",
            "workspace_id": "",
            "repo_root": str(workspace_dir),
            "backend": "opencode",
            "transport": "acp",
            "transport_session_id": "acp-session-1",
            "instruction": "base instruction",
            "status": "waiting_user",
            "summary": "waiting",
            "pending_question": "请选择是否继续",
            "result": {},
            "history": [],
            "log_path": str(tmp_path / "data" / "log.txt"),
            "created_at": "2026-03-13T00:00:00+08:00",
        }
    )
    assert created["session_id"] == "cs-acp"

    continued = await service.continue_session(
        session_id="cs-acp",
        user_reply="继续，直接改代码",
    )

    assert continued["ok"] is True
    assert continued["data"]["transport"] == "acp"
    assert continued["data"]["transport_session_id"] == "acp-session-2"
    assert captured["calls"]
    assert "继续，直接改代码" in captured["calls"][0]["instruction"]
    assert "Continue the previous ACP coding session" in captured["calls"][0]["instruction"]
    assert captured["calls"][0]["transport"] == "acp"
    assert captured["calls"][0]["transport_session_id"] == "acp-session-1"
