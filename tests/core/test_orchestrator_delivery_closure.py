from datetime import datetime
from types import SimpleNamespace

import pytest

import core.agent_orchestrator as orchestrator_module
from core.agent_orchestrator import AgentOrchestrator
from core.heartbeat_store import heartbeat_store
from core.platform.models import Chat, MessageType, UnifiedMessage, User


class DummyContext:
    def __init__(self, user_id: str = "u1"):
        self.message = UnifiedMessage(
            id="m1",
            platform="telegram",
            user=User(id=user_id, username="tester"),
            chat=Chat(id=user_id, type="private"),
            date=datetime.now(),
            type=MessageType.TEXT,
            text="test",
        )
        self.user_data = {}
        self.platform_ctx = None
        self.platform_event = None
        self._adapter = SimpleNamespace(can_update_message=True)
        self.replies = []

    async def reply(self, text, **kwargs):
        self.replies.append(text)
        return SimpleNamespace(id="reply")

    async def reply_document(self, *args, **kwargs):
        return SimpleNamespace(id="doc")

    async def edit_message(self, *args, **kwargs):
        return None

    async def send_chat_action(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_terminal_extension_short_circuit_marks_done(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_done"

    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        if event_callback:
            await event_callback("tool_call_started", {"name": "ext_deployment_manager"})
            directive = await event_callback(
                "tool_call_finished",
                {
                    "name": "ext_deployment_manager",
                    "ok": True,
                    "summary": "deployment complete",
                    "terminal": True,
                    "task_outcome": "done",
                    "terminal_text": "✅ 部署成功并可访问。",
                },
            )
            if isinstance(directive, dict) and directive.get("stop"):
                yield directive.get("final_text", "")
                return
        yield "should-not-be-returned"

    monkeypatch.setattr(orchestrator.ai_service, "generate_response_stream", fake_stream)
    monkeypatch.setattr(orchestrator.extension_router, "route", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator_module, "MCP_MEMORY_ENABLED", False)

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert chunks == ["✅ 部署成功并可访问。"]
    state = await heartbeat_store.get_state(user_id)
    assert state["status"]["session"]["active_task"] is None
    session_events = state["status"]["session"].get("events") or []
    assert any("terminal_tool_done" in event for event in session_events)


@pytest.mark.asyncio
async def test_terminal_partial_sets_waiting_user(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_partial"

    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        if event_callback:
            await event_callback("tool_call_started", {"name": "ext_deployment_manager"})
            directive = await event_callback(
                "tool_call_finished",
                {
                    "name": "ext_deployment_manager",
                    "ok": True,
                    "summary": "already deployed",
                    "terminal": True,
                    "task_outcome": "partial",
                    "terminal_text": "ℹ️ 已检测到服务已部署。",
                },
            )
            if isinstance(directive, dict) and directive.get("stop"):
                yield directive.get("final_text", "")
                return
        yield "should-not-be-returned"

    monkeypatch.setattr(orchestrator.ai_service, "generate_response_stream", fake_stream)
    monkeypatch.setattr(orchestrator.extension_router, "route", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator_module, "MCP_MEMORY_ENABLED", False)

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署 n8n"}]}]
    chunks = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert len(chunks) == 1
    assert "3分钟内有效" in chunks[0]
    state = await heartbeat_store.get_state(user_id)
    active = state["status"]["session"]["active_task"]
    assert active is not None
    assert active["status"] == "waiting_user"
    assert active["confirmation_deadline"]
    assert ctx.user_data.get("pending_ui")


@pytest.mark.asyncio
async def test_terminal_extension_failure_short_circuits_without_loop(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_failed"

    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        if event_callback:
            await event_callback("tool_call_started", {"name": "ext_deployment_manager"})
            directive = await event_callback(
                "tool_call_finished",
                {
                    "name": "ext_deployment_manager",
                    "ok": False,
                    "summary": "deployment failed",
                    "terminal": True,
                    "task_outcome": "failed",
                    "terminal_text": "❌ 部署失败：compose 文件无效。",
                    "failure_mode": "fatal",
                },
            )
            if isinstance(directive, dict) and directive.get("stop"):
                yield directive.get("final_text", "")
                return
        yield "should-not-be-returned"

    monkeypatch.setattr(orchestrator.ai_service, "generate_response_stream", fake_stream)
    monkeypatch.setattr(orchestrator.extension_router, "route", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator_module, "MCP_MEMORY_ENABLED", False)

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert chunks == ["❌ 部署失败：compose 文件无效。"]
    state = await heartbeat_store.get_state(user_id)
    session_events = state["status"]["session"].get("events") or []
    assert any("terminal_tool_failed" in event for event in session_events)


@pytest.mark.asyncio
async def test_recoverable_terminal_failure_allows_auto_recovery(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_recoverable"

    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        if event_callback:
            await event_callback("tool_call_started", {"name": "ext_deployment_manager"})
            directive = await event_callback(
                "tool_call_finished",
                {
                    "name": "ext_deployment_manager",
                    "ok": False,
                    "summary": "env missing",
                    "terminal": True,
                    "task_outcome": "failed",
                    "terminal_text": "❌ 缺少 .env 文件。",
                    "failure_mode": "recoverable",
                },
            )
            # Recoverable failures should not stop immediately.
            assert not (isinstance(directive, dict) and directive.get("stop"))
            await event_callback("final_response", {"turn": 2, "text_preview": "已自动修复并完成部署"})
        yield "已自动修复并完成部署"

    monkeypatch.setattr(orchestrator.ai_service, "generate_response_stream", fake_stream)
    monkeypatch.setattr(orchestrator.extension_router, "route", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator_module, "MCP_MEMORY_ENABLED", False)

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert chunks == ["已自动修复并完成部署"]
    state = await heartbeat_store.get_state(user_id)
    assert state["status"]["session"]["active_task"] is None


@pytest.mark.asyncio
async def test_recoverable_terminal_failure_fails_after_recovery_budget(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_recoverable_budget"

    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()
    monkeypatch.setattr(orchestrator_module, "AUTO_RECOVERY_MAX_ATTEMPTS", 2)

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        if event_callback:
            await event_callback("tool_call_started", {"name": "ext_deployment_manager"})
            directive1 = await event_callback(
                "tool_call_finished",
                {
                    "name": "ext_deployment_manager",
                    "ok": False,
                    "summary": "env missing",
                    "terminal": True,
                    "task_outcome": "failed",
                    "terminal_text": "❌ 缺少 .env 文件。",
                    "failure_mode": "recoverable",
                },
            )
            assert not (isinstance(directive1, dict) and directive1.get("stop"))

            await event_callback("tool_call_started", {"name": "ext_deployment_manager"})
            directive2 = await event_callback(
                "tool_call_finished",
                {
                    "name": "ext_deployment_manager",
                    "ok": False,
                    "summary": "port conflict",
                    "terminal": True,
                    "task_outcome": "failed",
                    "terminal_text": "❌ 端口冲突，自动恢复预算已耗尽。",
                    "failure_mode": "recoverable",
                },
            )
            assert isinstance(directive2, dict) and directive2.get("stop") is True
            yield directive2.get("final_text", "")
            return
        yield "should-not-be-returned"

    monkeypatch.setattr(orchestrator.ai_service, "generate_response_stream", fake_stream)
    monkeypatch.setattr(orchestrator.extension_router, "route", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator_module, "MCP_MEMORY_ENABLED", False)

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert chunks == ["❌ 端口冲突，自动恢复预算已耗尽。"]
    state = await heartbeat_store.get_state(user_id)
    session_events = state["status"]["session"].get("events") or []
    assert any("recoverable_terminal_failure" in event for event in session_events)
    assert any("terminal_tool_failed" in event for event in session_events)
    assert state["status"]["session"]["active_task"] is None


@pytest.mark.asyncio
async def test_immediate_session_does_not_write_task_entries_to_heartbeat_md(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_no_task_queue"

    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        if event_callback:
            await event_callback("final_response", {"turn": 1, "text_preview": "处理完成"})
        yield "处理完成"

    monkeypatch.setattr(orchestrator.ai_service, "generate_response_stream", fake_stream)
    monkeypatch.setattr(orchestrator.extension_router, "route", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator_module, "MCP_MEMORY_ENABLED", False)

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "请立即处理一个普通任务"}]}]
    chunks = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert chunks == ["处理完成"]
    heartbeat_text = heartbeat_store.heartbeat_path(user_id).read_text(encoding="utf-8")
    assert "# Heartbeat checklist" in heartbeat_text
    assert "tasks:" not in heartbeat_text
    assert "## Events" not in heartbeat_text
