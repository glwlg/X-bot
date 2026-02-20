from types import SimpleNamespace

import pytest

from core.orchestrator_context import OrchestratorRuntimeContext
import core.orchestrator_context as context_module


def _runtime_context(**overrides) -> OrchestratorRuntimeContext:
    payload = {
        "user_id": "u-1",
        "user_data": {},
        "runtime_user_id": "u-1",
        "platform_name": "telegram",
        "worker_runtime_user": False,
        "heartbeat_runtime_user": False,
        "session_state_enabled": True,
        "runtime_policy_ctx": {"agent_kind": "manager"},
        "runtime_agent_kind": "manager",
        "manager_runtime": True,
        "task_id": "task-1",
        "task_inbox_id": "",
    }
    payload.update(overrides)
    return OrchestratorRuntimeContext(**payload)


@pytest.mark.asyncio
async def test_ensure_task_inbox_submits_user_chat_task(monkeypatch):
    captured = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(task_id="inbox-123")

    monkeypatch.setattr(context_module.task_inbox, "submit", fake_submit)

    runtime_ctx = _runtime_context()
    task_inbox_id = await runtime_ctx.ensure_task_inbox(task_goal="帮我检查这个项目")

    assert task_inbox_id == "inbox-123"
    assert runtime_ctx.task_inbox_id == "inbox-123"
    assert captured["source"] == "user_chat"
    assert captured["goal"] == "帮我检查这个项目"
    assert captured["user_id"] == "u-1"
    assert captured["requires_reply"] is True
    assert captured["payload"]["task_id"] == "task-1"
    assert captured["payload"]["runtime_user_id"] == "u-1"
    assert captured["payload"]["platform"] == "telegram"


@pytest.mark.asyncio
async def test_ensure_task_inbox_skips_when_session_state_disabled(monkeypatch):
    called = False

    async def fake_submit(**_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(task_id="inbox-ignored")

    monkeypatch.setattr(context_module.task_inbox, "submit", fake_submit)

    runtime_ctx = _runtime_context(
        platform_name="worker_runtime",
        worker_runtime_user=True,
        session_state_enabled=False,
        manager_runtime=False,
        runtime_agent_kind="worker",
    )
    task_inbox_id = await runtime_ctx.ensure_task_inbox(task_goal="后台任务")

    assert task_inbox_id == ""
    assert runtime_ctx.task_inbox_id == ""
    assert called is False


@pytest.mark.asyncio
async def test_mark_manager_loop_started_updates_task_inbox(monkeypatch):
    calls = []

    async def fake_update_status(task_id, status, **kwargs):
        calls.append((task_id, status, kwargs))
        return True

    monkeypatch.setattr(context_module.task_inbox, "update_status", fake_update_status)

    runtime_ctx = _runtime_context(task_inbox_id="inbox-1")
    await runtime_ctx.mark_manager_loop_started("请求摘要")

    assert len(calls) == 1
    task_id, status, kwargs = calls[0]
    assert task_id == "inbox-1"
    assert status == "running"
    assert kwargs["event"] == "manager_loop_started"
    assert kwargs["manager_id"] == "core-manager"
