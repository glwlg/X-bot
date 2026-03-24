from types import SimpleNamespace

import pytest

from core.skill_tool_handlers import skill_tool_handler_registry


@pytest.mark.asyncio
async def test_manager_gh_cli_handler_forwards_notify_target(monkeypatch):
    captured = {}

    async def fake_gh_cli(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "summary": "ok", "text": "ok", "terminal": True}

    monkeypatch.setattr("core.skill_tool_handlers.gh_tools.gh_cli", fake_gh_cli)

    dispatcher = SimpleNamespace(
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                platform="telegram",
                user=SimpleNamespace(id="user-7"),
                chat=SimpleNamespace(id="chat-7"),
            ),
            user_data={},
        )
    )

    result = await skill_tool_handler_registry.dispatch(
        "manager.gh_cli",
        dispatcher=dispatcher,
        args={"action": "auth_start", "hostname": "github.com"},
    )

    assert result["ok"] is True
    assert captured["action"] == "auth_start"
    assert captured["hostname"] == "github.com"
    assert captured["notify_platform"] == "telegram"
    assert captured["notify_chat_id"] == "chat-7"
    assert captured["notify_user_id"] == "user-7"


@pytest.mark.asyncio
async def test_manager_repo_workspace_handler_routes_prepare(monkeypatch):
    captured = {}

    async def fake_repo_workspace(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "summary": "ok", "text": "ok", "terminal": False}

    monkeypatch.setattr(
        "core.skill_tool_handlers.repo_workspace_tools.repo_workspace",
        fake_repo_workspace,
    )

    dispatcher = SimpleNamespace(
        ctx=SimpleNamespace(message=SimpleNamespace(text=""), user_data={})
    )

    result = await skill_tool_handler_registry.dispatch(
        "manager.repo_workspace",
        dispatcher=dispatcher,
        args={"action": "prepare", "repo_url": "https://github.com/acme/project.git"},
    )

    assert result["ok"] is True
    assert captured["action"] == "prepare"
    assert captured["repo_url"] == "https://github.com/acme/project.git"


@pytest.mark.asyncio
async def test_manager_codex_session_handler_uses_user_request_as_instruction(
    monkeypatch,
):
    captured = {}

    async def fake_codex_session(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "summary": "ok", "text": "ok", "terminal": False}

    monkeypatch.setattr(
        "core.skill_tool_handlers.codex_tools.codex_session",
        fake_codex_session,
    )

    dispatcher = SimpleNamespace(
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="请在当前仓库里实现一个新 skill"),
            user_data={},
        ),
        _extract_user_request=lambda: "请在当前仓库里实现一个新 skill",
    )

    result = await skill_tool_handler_registry.dispatch(
        "manager.codex_session",
        dispatcher=dispatcher,
        args={"action": "start", "workspace_id": "ws-1", "instruction": ""},
    )

    assert result["ok"] is True
    assert captured["action"] == "start"
    assert captured["workspace_id"] == "ws-1"
    assert captured["instruction"] == "请在当前仓库里实现一个新 skill"


@pytest.mark.asyncio
async def test_manager_git_ops_handler_routes_commit(monkeypatch):
    captured = {}

    async def fake_git_ops(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "summary": "ok", "text": "ok", "terminal": False}

    monkeypatch.setattr("core.skill_tool_handlers.git_tools.git_ops", fake_git_ops)

    dispatcher = SimpleNamespace(
        ctx=SimpleNamespace(message=SimpleNamespace(text=""), user_data={})
    )

    result = await skill_tool_handler_registry.dispatch(
        "manager.git_ops",
        dispatcher=dispatcher,
        args={"action": "commit", "workspace_id": "ws-1", "message": "feat: update"},
    )

    assert result["ok"] is True
    assert captured["action"] == "commit"
    assert captured["workspace_id"] == "ws-1"
    assert captured["message"] == "feat: update"
