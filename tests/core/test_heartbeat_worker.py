from types import SimpleNamespace

import pytest

import core.heartbeat_worker as heartbeat_worker_module
from core.heartbeat_store import heartbeat_store
from core.heartbeat_worker import HeartbeatWorker


@pytest.mark.asyncio
async def test_heartbeat_worker_manual_run_suppresses_ok(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    await heartbeat_store.set_heartbeat_spec(
        "worker_u1",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.set_delivery_target("worker_u1", "discord", "42")

    async def fake_handle_message(ctx, message_history):
        yield "HEARTBEAT_OK"

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type("FakeOrchestrator", (), {"handle_message": fake_handle_message})(),
    )

    sent = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.suppress_ok = True

    result = await worker.run_user_now("worker_u1")
    assert result == "HEARTBEAT_OK"
    assert sent == []

    state = await heartbeat_store.get_state("worker_u1")
    assert state["status"]["heartbeat"]["last_result"] == "HEARTBEAT_OK"


@pytest.mark.asyncio
async def test_heartbeat_worker_manual_run_pushes_non_ok(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    await heartbeat_store.set_heartbeat_spec(
        "worker_u2",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.set_delivery_target("worker_u2", "discord", "99")

    async def fake_handle_message(ctx, message_history):
        yield "请检查收件箱中 1 封紧急邮件。"

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type("FakeOrchestrator", (), {"handle_message": fake_handle_message})(),
    )

    sent = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.suppress_ok = True
    worker.readonly_dispatch = False

    result = await worker.run_user_now("worker_u2")
    assert "紧急邮件" in result
    assert sent and sent[0][0] == "99"
    assert "紧急邮件" in sent[0][1]


@pytest.mark.asyncio
async def test_heartbeat_worker_readonly_action_does_not_dispatch_to_worker(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    await heartbeat_store.set_heartbeat_spec(
        "worker_u3",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.set_delivery_target("worker_u3", "discord", "100")

    async def fake_handle_message(ctx, message_history):
        yield "检测到需要修复的配置异常。"

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type("FakeOrchestrator", (), {"handle_message": fake_handle_message})(),
    )

    sent = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.mode = "readonly"
    worker.readonly_dispatch = True
    worker.suppress_ok = True

    result = await worker.run_user_now("worker_u3")
    assert "不会派发 Worker 执行层修复任务" in result
    assert "Core Manager 治理提醒" in result
    assert sent and sent[0][0] == "100"
