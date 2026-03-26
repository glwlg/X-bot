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
        "subagent_runtime_user": False,
        "heartbeat_runtime_user": False,
        "session_state_enabled": True,
        "runtime_policy_ctx": {"agent_kind": "core-ikaros"},
        "runtime_agent_kind": "core-ikaros",
        "ikaros_runtime": True,
        "task_id": "task-1",
        "task_inbox_id": "",
        "session_id": "",
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

    runtime_ctx = _runtime_context(session_id="sess-123")
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
    assert captured["payload"]["session_id"] == "sess-123"
    assert captured["metadata"]["session_id"] == "sess-123"


@pytest.mark.asyncio
async def test_ensure_task_inbox_skips_when_session_state_disabled(monkeypatch):
    called = False

    async def fake_submit(**_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(task_id="inbox-ignored")

    monkeypatch.setattr(context_module.task_inbox, "submit", fake_submit)

    runtime_ctx = _runtime_context(
        platform_name="subagent_kernel",
        subagent_runtime_user=True,
        session_state_enabled=False,
        ikaros_runtime=False,
        runtime_agent_kind="subagent",
    )
    task_inbox_id = await runtime_ctx.ensure_task_inbox(task_goal="后台任务")

    assert task_inbox_id == ""
    assert runtime_ctx.task_inbox_id == ""
    assert called is False


@pytest.mark.asyncio
async def test_ensure_task_inbox_uses_heartbeat_source_when_enabled(monkeypatch):
    captured = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(task_id="hb-inbox-1")

    monkeypatch.setattr(context_module.task_inbox, "submit", fake_submit)

    runtime_ctx = _runtime_context(
        platform_name="heartbeat_daemon",
        heartbeat_runtime_user=True,
        session_state_enabled=True,
    )
    task_inbox_id = await runtime_ctx.ensure_task_inbox(task_goal="检查今日 heartbeat 项")

    assert task_inbox_id == "hb-inbox-1"
    assert captured["source"] == "heartbeat"
    assert captured["goal"] == "检查今日 heartbeat 项"


@pytest.mark.asyncio
async def test_mark_ikaros_loop_started_updates_task_inbox(monkeypatch):
    calls = []

    async def fake_update_status(task_id, status, **kwargs):
        calls.append((task_id, status, kwargs))
        return True

    monkeypatch.setattr(context_module.task_inbox, "update_status", fake_update_status)

    runtime_ctx = _runtime_context(task_inbox_id="inbox-1")
    await runtime_ctx.mark_ikaros_loop_started("请求摘要")

    assert len(calls) == 1
    task_id, status, kwargs = calls[0]
    assert task_id == "inbox-1"
    assert status == "running"
    assert kwargs["event"] == "ikaros_loop_started"
    assert kwargs["ikaros_id"] == "core-ikaros"


@pytest.mark.asyncio
async def test_activate_session_marks_heartbeat_source(monkeypatch):
    calls = []

    async def fake_set_session_active_task(user_id, payload):
        calls.append((user_id, payload))
        return payload

    monkeypatch.setattr(
        context_module.heartbeat_store,
        "set_session_active_task",
        fake_set_session_active_task,
    )
    monkeypatch.setattr(context_module.task_manager, "set_heartbeat_path", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(context_module.task_manager, "set_active_task_id", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(context_module.task_manager, "heartbeat", lambda *_args, **_kwargs: True)

    runtime_ctx = _runtime_context(
        task_id="hb-run-1",
        task_inbox_id="hb-inbox-1",
        platform_name="heartbeat_daemon",
        heartbeat_runtime_user=True,
        session_state_enabled=True,
    )

    async def fake_append_session_event(_note: str) -> None:
        return None

    runtime_ctx.append_session_event = fake_append_session_event  # type: ignore[method-assign]
    await runtime_ctx.activate_session(task_goal="检查 heartbeat", task_workspace_root="")

    assert calls
    assert calls[0][0] == "u-1"
    assert calls[0][1]["source"] == "heartbeat"
