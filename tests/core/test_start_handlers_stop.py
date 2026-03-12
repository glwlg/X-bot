from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.heartbeat_store as heartbeat_store_module
import core.task_manager as task_manager_module
import handlers.start_handlers as start_handlers
import shared.queue.dispatch_queue as dispatch_queue_module
from core.platform.models import Chat, MessageType, UnifiedMessage, User


class _DummyContext:
    def __init__(self, user_id: str, text: str = "/stop"):
        self.message = UnifiedMessage(
            id="m1",
            platform="telegram",
            user=User(id=user_id, username="tester"),
            chat=Chat(id=user_id, type="private"),
            date=datetime.now(),
            type=MessageType.TEXT,
            text=text,
        )
        self.replies: list[str] = []
        self.callback_data = ""
        self.callback_user_id = user_id

    async def reply(self, text, **kwargs):
        _ = kwargs
        self.replies.append(str(text))
        return SimpleNamespace(id="reply")

    async def answer_callback(self):
        return True


class _FakeTaskManager:
    def __init__(self, *, active_info=None, cancelled_desc=None):
        self.active_info = active_info
        self.cancelled_desc = cancelled_desc
        self.cancel_calls: list[str] = []

    def get_task_info(self, user_id: str):
        _ = user_id
        return self.active_info

    async def cancel_task(self, user_id: str):
        self.cancel_calls.append(str(user_id))
        return self.cancelled_desc


class _FakeHeartbeatStore:
    def __init__(self, active_task=None):
        self.active_task = active_task
        self.updated: list[tuple[str, dict]] = []
        self.released: list[str] = []
        self.events: list[tuple[str, str]] = []

    async def get_session_active_task(self, user_id: str):
        _ = user_id
        return self.active_task

    def heartbeat_path(self, user_id: str):
        return Path(f"/tmp/{user_id}-heartbeat.md")

    async def update_session_active_task(self, user_id: str, **kwargs):
        self.updated.append((str(user_id), dict(kwargs)))

    async def release_lock(self, user_id: str):
        self.released.append(str(user_id))

    async def append_session_event(self, user_id: str, event: str):
        self.events.append((str(user_id), str(event)))


class _FakeDispatchQueue:
    def __init__(self, result):
        self.result = dict(result)
        self.calls: list[dict] = []

    async def cancel_for_user(
        self, *, user_id: str, reason: str, include_running: bool
    ):
        self.calls.append(
            {
                "user_id": str(user_id),
                "reason": str(reason),
                "include_running": bool(include_running),
            }
        )
        return dict(self.result)


class _FakeSessionTaskStore:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    async def get_active(self, _user_id: str):
        return self.snapshot

    async def get(self, _task_id: str):
        return self.snapshot


@pytest.mark.asyncio
async def test_stop_command_cancels_worker_tasks_and_updates_heartbeat(monkeypatch):
    async def _allow(_ctx):
        return True

    monkeypatch.setattr(start_handlers, "check_permission_unified", _allow)

    fake_task_manager = _FakeTaskManager(
        active_info={
            "todo_path": "/tmp/todo.md",
            "heartbeat_path": "/tmp/heartbeat.md",
            "active_task_id": "hb-1",
        },
        cancelled_desc="worker_dispatch",
    )
    fake_heartbeat_store = _FakeHeartbeatStore(active_task=None)
    fake_dispatch_queue = _FakeDispatchQueue(
        {
            "pending_cancelled": 2,
            "running_signaled": 1,
            "job_ids": ["j-1", "j-2", "j-3"],
        }
    )

    monkeypatch.setattr(task_manager_module, "task_manager", fake_task_manager)
    monkeypatch.setattr(heartbeat_store_module, "heartbeat_store", fake_heartbeat_store)
    monkeypatch.setattr(
        dispatch_queue_module,
        "dispatch_queue",
        fake_dispatch_queue,
    )
    monkeypatch.setattr(
        start_handlers,
        "session_task_store",
        _FakeSessionTaskStore(),
    )

    ctx = _DummyContext("u-stop")
    await start_handlers.stop_command(ctx)

    assert fake_task_manager.cancel_calls == ["u-stop"]
    assert fake_dispatch_queue.calls == [
        {
            "user_id": "u-stop",
            "reason": "cancelled_by_stop_command",
            "include_running": True,
        }
    ]
    assert fake_heartbeat_store.updated
    assert fake_heartbeat_store.released == ["u-stop"]
    assert fake_heartbeat_store.events == [("u-stop", "user_cancelled:hb-1")]

    assert len(ctx.replies) == 2
    final_text = ctx.replies[-1]
    assert "已中断任务" in final_text
    assert "取消排队 2 个" in final_text
    assert "中断运行 1 个" in final_text


@pytest.mark.asyncio
async def test_stop_command_reports_no_active_task(monkeypatch):
    async def _allow(_ctx):
        return True

    monkeypatch.setattr(start_handlers, "check_permission_unified", _allow)

    fake_task_manager = _FakeTaskManager(active_info=None, cancelled_desc=None)
    fake_heartbeat_store = _FakeHeartbeatStore(active_task=None)
    fake_dispatch_queue = _FakeDispatchQueue(
        {
            "pending_cancelled": 0,
            "running_signaled": 0,
            "job_ids": [],
        }
    )

    monkeypatch.setattr(task_manager_module, "task_manager", fake_task_manager)
    monkeypatch.setattr(heartbeat_store_module, "heartbeat_store", fake_heartbeat_store)
    monkeypatch.setattr(
        dispatch_queue_module,
        "dispatch_queue",
        fake_dispatch_queue,
    )
    monkeypatch.setattr(
        start_handlers,
        "session_task_store",
        _FakeSessionTaskStore(),
    )

    ctx = _DummyContext("u-idle")
    await start_handlers.stop_command(ctx)

    assert fake_task_manager.cancel_calls == ["u-idle"]
    assert len(ctx.replies) == 2
    assert "当前没有正在执行的任务" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_stop_command_renders_session_brief_when_available(monkeypatch):
    async def _allow(_ctx):
        return True

    monkeypatch.setattr(start_handlers, "check_permission_unified", _allow)

    fake_task_manager = _FakeTaskManager(
        active_info={
            "todo_path": "/tmp/todo.md",
            "heartbeat_path": "/tmp/heartbeat.md",
            "active_task_id": "tsk-session-1",
        },
        cancelled_desc="worker_dispatch",
    )
    fake_heartbeat_store = _FakeHeartbeatStore(active_task=None)
    fake_dispatch_queue = _FakeDispatchQueue(
        {
            "pending_cancelled": 0,
            "running_signaled": 1,
            "job_ids": ["j-1"],
        }
    )

    snapshot = SimpleNamespace(
        session_task_id="tsk-session-1",
        stage_index=2,
        stage_total=3,
        stage_title="验证结果并整理交付",
    )

    monkeypatch.setattr(task_manager_module, "task_manager", fake_task_manager)
    monkeypatch.setattr(heartbeat_store_module, "heartbeat_store", fake_heartbeat_store)
    monkeypatch.setattr(
        dispatch_queue_module,
        "dispatch_queue",
        fake_dispatch_queue,
    )
    monkeypatch.setattr(
        start_handlers,
        "session_task_store",
        _FakeSessionTaskStore(snapshot=snapshot),
    )

    ctx = _DummyContext("u-stop-brief")
    await start_handlers.stop_command(ctx)

    final_text = ctx.replies[-1]
    assert "任务：`tsk-session-1`" in final_text
    assert "阶段：2/3 - 验证结果并整理交付" in final_text


@pytest.mark.asyncio
async def test_button_callback_continue_resumes_waiting_task(monkeypatch):
    async def _allow(_ctx):
        return True

    monkeypatch.setattr(start_handlers, "check_permission_unified", _allow)

    fake_heartbeat_store = _FakeHeartbeatStore(
        active_task={"id": "mgr-continue", "status": "waiting_user"}
    )

    class _FakeClosureService:
        def __init__(self):
            self.calls: list[dict] = []

        async def resume_waiting_task(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {"ok": True, "message": "✅ 已恢复执行，正在继续推进阶段 2/3。"}

    fake_service = _FakeClosureService()

    monkeypatch.setattr(heartbeat_store_module, "heartbeat_store", fake_heartbeat_store)
    monkeypatch.setattr(
        "manager.relay.closure_service.manager_closure_service",
        fake_service,
    )
    monkeypatch.setattr(
        start_handlers,
        "session_task_store",
        _FakeSessionTaskStore(),
    )

    ctx = _DummyContext("u-callback", text="noop")
    ctx.callback_data = "task_continue"

    result = await start_handlers.button_callback(ctx)

    assert result == start_handlers.CONVERSATION_END
    assert fake_service.calls == [
        {
            "user_id": "u-callback",
            "user_message": "continue",
            "source": "button",
        }
    ]
    assert fake_heartbeat_store.events == [
        ("u-callback", "user_confirm_continue:mgr-continue")
    ]
    assert "已恢复执行" in ctx.replies[-1]
