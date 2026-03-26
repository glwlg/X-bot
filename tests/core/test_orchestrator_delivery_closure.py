from datetime import datetime
from types import SimpleNamespace

import pytest

import core.agent_orchestrator as orchestrator_module
from core.agent_orchestrator import AgentOrchestrator
from core.heartbeat_store import heartbeat_store
from core.platform.models import Chat, MessageType, UnifiedMessage, User
from core.task_inbox import task_inbox
from core.task_tracker_service import task_tracker_service
from services.intent_router import RoutingDecision


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


def _reset_task_inbox(tmp_path):
    root = (tmp_path / "task_inbox").resolve()
    tasks_root = (root / "tasks").resolve()
    archive_root = (root / "archive").resolve()
    events_path = (root / "events.jsonl").resolve()
    tasks_root.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)
    task_inbox.persist = True
    task_inbox.root = root
    task_inbox.tasks_root = tasks_root
    task_inbox.archive_root = archive_root
    task_inbox.events_path = events_path
    task_inbox._loaded = False
    task_inbox._tasks = {}


@pytest.fixture(autouse=True)
def _stub_intent_router(monkeypatch):
    async def fake_route(**_kwargs):
        return RoutingDecision(
            request_mode="task",
            candidate_skills=[],
            reason="task",
            confidence=0.9,
        )

    monkeypatch.setattr(orchestrator_module.intent_router, "route", fake_route)


@pytest.mark.asyncio
async def test_terminal_extension_short_circuit_marks_done(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_done"

    _reset_task_inbox(tmp_path)
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
            await event_callback(
                "tool_call_started", {"name": "ext_deployment_manager"}
            )
            directive = await event_callback(
                "tool_call_finished",
                {
                    "name": "ext_deployment_manager",
                    "ok": True,
                    "summary": "deployment complete",
                    "terminal": True,
                    "task_outcome": "done",
                    "terminal_text": "✅ 部署成功并可访问。",
                    "terminal_ui": {
                        "actions": [
                            [{"text": "查看日志", "callback_data": "deploy_logs"}]
                        ]
                    },
                },
            )
            if isinstance(directive, dict) and directive.get("stop"):
                yield directive.get("final_text", "")
                return
        yield "should-not-be-returned"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["✅ 部署成功并可访问。"]
    pending_ui = ctx.user_data.get("pending_ui")
    assert isinstance(pending_ui, list)
    assert pending_ui[0]["actions"][0][0]["text"] == "查看日志"
    state = await heartbeat_store.get_state(user_id)
    assert state["status"]["session"]["active_task"] is None
    session_events = state["status"]["session"].get("events") or []
    assert any("terminal_tool_done" in event for event in session_events)


@pytest.mark.asyncio
async def test_terminal_partial_sets_waiting_user(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_partial"

    _reset_task_inbox(tmp_path)
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
            await event_callback(
                "tool_call_started", {"name": "ext_deployment_manager"}
            )
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

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署 n8n"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert len(chunks) == 1
    assert "3分钟内有效" in chunks[0]
    state = await heartbeat_store.get_state(user_id)
    active = state["status"]["session"]["active_task"]
    assert active is not None
    assert active["status"] == "waiting_user"
    assert active["confirmation_deadline"]
    assert ctx.user_data.get("pending_ui")


@pytest.mark.asyncio
async def test_terminal_extension_failure_short_circuits_without_loop(
    monkeypatch, tmp_path
):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_failed"

    _reset_task_inbox(tmp_path)
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
            await event_callback(
                "tool_call_started", {"name": "ext_deployment_manager"}
            )
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

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["❌ 部署失败：compose 文件无效。"]
    state = await heartbeat_store.get_state(user_id)
    session_events = state["status"]["session"].get("events") or []
    assert any("terminal_tool_failed" in event for event in session_events)


@pytest.mark.asyncio
async def test_recoverable_terminal_failure_allows_auto_recovery(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_terminal_recoverable"

    _reset_task_inbox(tmp_path)
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
            await event_callback(
                "tool_call_started", {"name": "ext_deployment_manager"}
            )
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
            await event_callback(
                "final_response", {"turn": 2, "text_preview": "已自动修复并完成部署"}
            )
        yield "已自动修复并完成部署"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["已自动修复并完成部署"]
    state = await heartbeat_store.get_state(user_id)
    assert state["status"]["session"]["active_task"] is None


@pytest.mark.asyncio
async def test_recoverable_terminal_failure_fails_after_recovery_budget(
    monkeypatch, tmp_path
):
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
            await event_callback(
                "tool_call_started", {"name": "ext_deployment_manager"}
            )
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

            await event_callback(
                "tool_call_started", {"name": "ext_deployment_manager"}
            )
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

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "帮我部署服务"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["❌ 端口冲突，自动恢复预算已耗尽。"]
    state = await heartbeat_store.get_state(user_id)
    session_events = state["status"]["session"].get("events") or []
    assert any("recoverable_terminal_failure" in event for event in session_events)
    assert any("terminal_tool_failed" in event for event in session_events)
    assert state["status"]["session"]["active_task"] is None


@pytest.mark.asyncio
async def test_immediate_session_does_not_write_task_entries_to_heartbeat_md(
    monkeypatch, tmp_path
):
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
            await event_callback(
                "final_response", {"turn": 1, "text_preview": "处理完成"}
            )
        yield "处理完成"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    message_history = [{"role": "user", "parts": [{"text": "请立即处理一个普通任务"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["处理完成"]
    heartbeat_text = heartbeat_store.heartbeat_path(user_id).read_text(encoding="utf-8")
    assert "# Heartbeat checklist" in heartbeat_text
    assert "tasks:" not in heartbeat_text
    assert "## Events" not in heartbeat_text


@pytest.mark.asyncio
async def test_ikaros_progress_callback_receives_tool_events(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    user_id = "u_ikaros_progress"

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
        del message_history, tools, tool_executor, system_instruction
        if event_callback:
            await event_callback("turn_start", {"turn": 1})
            await event_callback(
                "tool_call_started",
                {
                    "turn": 1,
                    "name": "bash",
                    "args": {"command": "docker stop uptime-kuma"},
                },
            )
            await event_callback(
                "tool_call_finished",
                {
                    "turn": 1,
                    "name": "bash",
                    "ok": True,
                    "summary": "Command executed with code 0",
                },
            )
            await event_callback(
                "final_response",
                {
                    "turn": 1,
                    "text_preview": "已停止 uptime-kuma。",
                },
            )
        yield "已停止 uptime-kuma。"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    ikaros_events: list[dict[str, str]] = []

    async def _progress_callback(snapshot):
        ikaros_events.append(dict(snapshot or {}))

    ctx.user_data["ikaros_progress_callback"] = _progress_callback
    message_history = [{"role": "user", "parts": [{"text": "停止 uptime-kuma"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["已停止 uptime-kuma。"]
    assert any(
        event.get("event") == "tool_call_started"
        and event.get("name") == "bash"
        and dict(event.get("args") or {}).get("command") == "docker stop uptime-kuma"
        and str(event.get("task_id") or "").strip()
        for event in ikaros_events
    )
    assert any(
        event.get("event") == "tool_call_finished"
        and event.get("summary") == "Command executed with code 0"
        for event in ikaros_events
    )
    assert any(
        event.get("event") == "final_response"
        and event.get("text_preview") == "已停止 uptime-kuma。"
        for event in ikaros_events
    )


@pytest.mark.asyncio
async def test_subagent_progress_callback_receives_terminal_payload(
    monkeypatch, tmp_path
):
    orchestrator = AgentOrchestrator()
    user_id = "u_subagent_progress_terminal_payload"

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
        del message_history, tools, tool_executor, system_instruction
        if event_callback:
            await event_callback(
                "tool_call_finished",
                {
                    "turn": 2,
                    "name": "bash",
                    "ok": True,
                    "summary": "✅ 图片已生成。",
                    "terminal": True,
                    "task_outcome": "done",
                    "terminal_text": "✅ 图片已生成。\n📏 比例: 1:1",
                    "terminal_payload": {
                        "text": "✅ 图片已生成。\n📏 比例: 1:1",
                        "files": [
                            {
                                "kind": "photo",
                                "path": "/tmp/demo.png",
                                "filename": "demo.png",
                            }
                        ],
                    },
                },
            )
        yield "✅ 图片已生成。\n📏 比例: 1:1"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    subagent_events: list[dict[str, object]] = []

    async def _progress_callback(snapshot):
        subagent_events.append(dict(snapshot or {}))

    ctx.user_data["subagent_progress_callback"] = _progress_callback
    message_history = [{"role": "user", "parts": [{"text": "请画图"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["✅ 图片已生成。\n📏 比例: 1:1"]
    assert any(
        event.get("event") == "tool_call_finished"
        and event.get("terminal") is True
        and isinstance(event.get("terminal_payload"), dict)
        and event["terminal_payload"]["files"][0]["filename"] == "demo.png"
        for event in subagent_events
    )


@pytest.mark.asyncio
async def test_final_response_keeps_waiting_external_task_open(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_waiting_external"

    _reset_task_inbox(tmp_path)
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    task = await task_inbox.submit(
        source="user_chat",
        goal="跟进这个 PR，直到合并",
        user_id=user_id,
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        updated = await task_tracker_service.update(
            user_id=user_id,
            task_id=task.task_id,
            status="waiting_external",
            result_summary="PR 已创建，等待合并。",
            done_when="PR merged",
            next_review_after="2026-03-13T15:00:00+08:00",
        )
        assert updated["ok"] is True
        if event_callback:
            await event_callback(
                "final_response",
                {
                    "turn": 1,
                    "text_preview": "PR 已创建，后续会继续跟进直到合并。",
                },
            )
        yield "PR 已创建，后续会继续跟进直到合并。"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    ctx.user_data["task_inbox_id"] = task.task_id
    ctx.user_data["runtime_task_id"] = "mgr-followup-1"
    message_history = [{"role": "user", "parts": [{"text": "继续跟进这个 PR"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["PR 已创建，后续会继续跟进直到合并。"]
    stored = await task_inbox.get(task.task_id)
    assert stored is not None
    assert stored.status == "waiting_external"
    state = await heartbeat_store.get_state(user_id)
    active = state["status"]["session"]["active_task"]
    assert active is not None
    assert active["status"] == "waiting_external"


@pytest.mark.asyncio
async def test_final_response_auto_keeps_pr_creation_task_open(monkeypatch, tmp_path):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    user_id = "u_pr_followup_auto"

    _reset_task_inbox(tmp_path)
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    task = await task_inbox.submit(
        source="user_chat",
        goal="提 PR 并持续跟进直到合并",
        user_id=user_id,
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        if event_callback:
            await event_callback(
                "final_response",
                {
                    "turn": 1,
                    "text_preview": "PR 已创建： https://github.com/Scenx/fuck-skill/pull/22 ，后续继续跟进。",
                },
            )
        yield "PR 已创建： https://github.com/Scenx/fuck-skill/pull/22 ，后续继续跟进。"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext(user_id=user_id)
    ctx.user_data["task_inbox_id"] = task.task_id
    ctx.user_data["runtime_task_id"] = "mgr-followup-pr-1"
    message_history = [{"role": "user", "parts": [{"text": "继续提 PR"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == [
        "PR 已创建： https://github.com/Scenx/fuck-skill/pull/22 ，后续继续跟进。"
    ]
    stored = await task_inbox.get(task.task_id)
    assert stored is not None
    assert stored.status == "waiting_external"
    followup = stored.metadata.get("followup") or {}
    assert followup.get("done_when") == "GitHub pull request merged"
    assert (
        followup.get("refs", {}).get("pr_url")
        == "https://github.com/Scenx/fuck-skill/pull/22"
    )
