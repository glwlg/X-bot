from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.agent_orchestrator as orchestrator_module
import core.heartbeat_worker as heartbeat_worker_module
import core.worker_runtime as worker_runtime_module
from core.agent_orchestrator import AgentOrchestrator
from core.heartbeat_store import heartbeat_store
from core.heartbeat_worker import HeartbeatWorker
from core.markdown_memory_store import markdown_memory_store
from core.task_inbox import task_inbox
from core.worker_runtime import WorkerRuntime


class _DummyContext:
    def __init__(self, *, user_id: str = "u-1", platform: str = "telegram"):
        self.message = SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            platform=platform,
            chat=SimpleNamespace(id=user_id),
            id=f"msg-{user_id}",
        )
        self.user_data = {}
        self.replies: list[tuple[object, dict]] = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(id="reply-1", message_id="reply-1")

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        self.replies.append(
            (document, {"filename": filename, "caption": caption, **kwargs})
        )
        return SimpleNamespace(id="doc-1")


def _reset_task_inbox(tmp_path, monkeypatch) -> None:
    inbox_root = (tmp_path / "task_inbox").resolve()
    tasks_root = (inbox_root / "tasks").resolve()
    events_path = (inbox_root / "events.jsonl").resolve()
    inbox_root.mkdir(parents=True, exist_ok=True)
    tasks_root.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(task_inbox, "root", inbox_root)
    monkeypatch.setattr(task_inbox, "tasks_root", tasks_root)
    monkeypatch.setattr(task_inbox, "events_path", events_path)
    task_inbox._loaded = True
    task_inbox._tasks = {}


@pytest.mark.asyncio
async def test_manager_memory_survives_new_session(tmp_path, monkeypatch):
    users_root = (tmp_path / "users").resolve()
    users_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(markdown_memory_store, "users_root", users_root)

    ok, detail = markdown_memory_store.remember(
        "memory-user",
        "请记住我最喜欢喝手冲咖啡",
        source="integration_test",
    )
    assert ok is True
    assert "手冲咖啡" in detail

    snapshot = markdown_memory_store.load_snapshot(
        "memory-user",
        include_daily=True,
        max_chars=4000,
    )
    assert "手冲咖啡" in snapshot


@pytest.mark.asyncio
async def test_manager_reply_uses_worker_name_and_hides_internal_worker_tokens(
    monkeypatch,
):
    orchestrator = AgentOrchestrator()
    captured_metadata: dict = {}

    async def fake_dispatch_worker(
        *,
        instruction: str,
        worker_id: str = "",
        backend: str = "",
        source: str = "manager_dispatch",
        metadata: dict | None = None,
    ):
        _ = instruction
        _ = worker_id
        _ = backend
        _ = source
        captured_metadata.update(dict(metadata or {}))
        return {
            "ok": True,
            "worker_id": "worker-main",
            "worker_name": "Nova Ops",
            "task_id": "wt-1",
            "backend": "core-agent",
            "result": "deployment complete",
            "summary": "部署完成",
            "error": "",
            "auto_selected": True,
            "selection_reason": "test",
            "runtime_mode": "local",
            "terminal": True,
            "task_outcome": "done",
        }

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = message_history
        _ = tools
        _ = system_instruction
        await tool_executor("dispatch_worker", {"instruction": "部署 n8n"})
        raw_text = "worker-main 已完成部署，worker_id=worker-main，backend=core-agent。"
        if event_callback:
            await event_callback("final_response", {"text_preview": raw_text})
        yield raw_text

    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        orchestrator_module.dispatch_tools, "dispatch_worker", fake_dispatch_worker
    )
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )

    ctx = _DummyContext(user_id="dispatch-user")
    chunks = [
        chunk
        async for chunk in orchestrator.handle_message(
            ctx,
            [{"role": "user", "parts": [{"text": "帮我部署 n8n"}]}],
        )
    ]
    answer = "".join(chunks)

    assert "Nova Ops" in answer
    assert "worker-main" not in answer
    assert "worker_id" not in answer.lower()
    assert "backend" not in answer.lower()
    assert captured_metadata.get("user_id") == "dispatch-user"


class _FakeWorkerRegistry:
    def __init__(self, workspace_root: str):
        self.worker = {
            "id": "worker-main",
            "name": "Nova Ops",
            "backend": "core-agent",
            "workspace_root": workspace_root,
            "status": "ready",
        }

    async def get_worker(self, worker_id: str):
        if worker_id == "worker-main":
            return dict(self.worker)
        return None

    async def update_worker(self, worker_id: str, **fields):
        if worker_id != "worker-main":
            return None
        self.worker.update(fields)
        return dict(self.worker)


class _FakeWorkerTaskStore:
    def __init__(self):
        self.updated: list[dict] = []

    async def create_task(self, **kwargs):
        return {
            "task_id": "wt-core-1",
            "created_at": "2026-02-16T00:00:00+00:00",
            **kwargs,
        }

    async def update_task(self, task_id: str, **fields):
        row = {"task_id": task_id, **fields}
        self.updated.append(row)
        return row


@pytest.mark.asyncio
async def test_worker_core_agent_can_use_tools(monkeypatch, tmp_path):
    fake_registry = _FakeWorkerRegistry(str(tmp_path / "workers" / "worker-main"))
    fake_store = _FakeWorkerTaskStore()
    monkeypatch.setattr(worker_runtime_module, "worker_registry", fake_registry)
    monkeypatch.setattr(worker_runtime_module, "worker_task_store", fake_store)
    monkeypatch.setattr(
        worker_runtime_module.tool_access_store,
        "is_backend_allowed",
        lambda **_kwargs: (True, {"reason": "test_override"}),
    )

    orchestrator = AgentOrchestrator()
    called_tools: list[tuple[str, dict]] = []

    async def fake_execute_core_tool(
        name,
        args,
        execution_policy,
        task_workspace_root,
    ):
        _ = execution_policy
        _ = task_workspace_root
        called_tools.append((str(name), dict(args or {})))
        return {"ok": True, "summary": "worker-ready", "result": "worker-ready"}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = message_history
        _ = tools
        _ = system_instruction
        tool_result = await tool_executor("bash", {"command": "echo worker-ready"})
        text = f"工具执行完成：{str(tool_result.get('summary') or 'ok')}"
        if event_callback:
            await event_callback("final_response", {"text_preview": text})
        yield text

    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        orchestrator.tool_broker, "execute_core_tool", fake_execute_core_tool
    )
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator_module, "agent_orchestrator", orchestrator)

    runtime = WorkerRuntime()
    runtime.runtime_mode = "local"

    result = await runtime.execute_task(
        worker_id="worker-main",
        source="user_chat",
        instruction="执行 worker 自检",
        backend="core-agent",
        metadata={"user_id": "tool-user"},
    )

    assert result["ok"] is True
    assert "工具执行完成" in str(result.get("result") or "")
    assert called_tools
    assert called_tools[0][0] == "bash"
    assert called_tools[0][1]["command"] == "echo worker-ready"


@pytest.mark.asyncio
async def test_heartbeat_dispatches_to_worker_and_delivers_manager_result(
    monkeypatch, tmp_path
):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()
    _reset_task_inbox(tmp_path, monkeypatch)

    user_id = "hb-user"
    await heartbeat_store.set_heartbeat_spec(
        user_id,
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.add_checklist_item(user_id, "检查今日待办与告警")
    await heartbeat_store.set_delivery_target(user_id, "discord", "target-42")

    orchestrator = AgentOrchestrator()

    async def fake_dispatch_worker(
        *,
        instruction: str,
        worker_id: str = "",
        backend: str = "",
        source: str = "manager_dispatch",
        metadata: dict | None = None,
    ):
        _ = instruction
        _ = worker_id
        _ = backend
        _ = source
        inbox_id = str((metadata or {}).get("task_inbox_id") or "")
        if inbox_id:
            await task_inbox.assign_worker(
                inbox_id,
                worker_id="worker-main",
                reason="heartbeat_dispatch_test",
            )
        return {
            "ok": True,
            "worker_id": "worker-main",
            "worker_name": "Nova Patrol",
            "task_id": "wt-hb-1",
            "backend": "core-agent",
            "result": "巡检完成",
            "summary": "巡检完成，无阻塞风险",
            "error": "",
            "auto_selected": True,
            "selection_reason": "heartbeat_test",
            "runtime_mode": "local",
            "terminal": True,
            "task_outcome": "done",
        }

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = message_history
        _ = tools
        _ = system_instruction
        dispatch = await tool_executor(
            "dispatch_worker",
            {"instruction": "执行 heartbeat 巡检"},
        )
        text = f"{str(dispatch.get('worker_name') or '执行助手')} 已完成：{str(dispatch.get('summary') or '')}"
        if event_callback:
            await event_callback("final_response", {"text_preview": text})
        yield text

    sent: list[tuple[str, str]] = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            _ = kwargs
            sent.append((str(chat_id), str(text)))
            return SimpleNamespace(id="sent-1")

    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        orchestrator_module.dispatch_tools, "dispatch_worker", fake_dispatch_worker
    )
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(heartbeat_worker_module, "agent_orchestrator", orchestrator)
    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.suppress_ok = True
    worker.readonly_dispatch = False

    result = await worker.run_user_now(user_id)

    assert "Nova Patrol" in result
    assert sent
    assert sent[0][0] == "target-42"
    assert "Nova Patrol" in sent[0][1]

    recent_tasks = await task_inbox.list_recent(user_id=user_id, limit=10)
    assert recent_tasks
    latest = recent_tasks[0]
    assert latest.source == "heartbeat"
    assert latest.status == "completed"
    assert latest.assigned_worker_id == "worker-main"
    assert "Nova Patrol" in str(latest.final_output or "")
