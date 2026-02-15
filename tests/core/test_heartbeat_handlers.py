from datetime import datetime
from types import SimpleNamespace

import pytest

import handlers.heartbeat_handlers as heartbeat_handlers
from core.heartbeat_store import heartbeat_store
from core.platform.models import Chat, MessageType, UnifiedMessage, User


class _DummyContext:
    def __init__(self, user_id: str, text: str):
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

    async def reply(self, text, **kwargs):
        self.replies.append(str(text))
        return SimpleNamespace(id="reply")


@pytest.mark.asyncio
async def test_heartbeat_command_add_list_remove(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    async def _allow(_ctx):
        return True

    monkeypatch.setattr(heartbeat_handlers, "check_permission_unified", _allow)

    ctx_add = _DummyContext("u1", "/heartbeat add Check email")
    await heartbeat_handlers.heartbeat_command(ctx_add)
    assert any("已添加" in text for text in ctx_add.replies)

    ctx_list = _DummyContext("u1", "/heartbeat list")
    await heartbeat_handlers.heartbeat_command(ctx_list)
    assert any("Check email" in text for text in ctx_list.replies)

    ctx_remove = _DummyContext("u1", "/heartbeat remove 1")
    await heartbeat_handlers.heartbeat_command(ctx_remove)
    assert any("已更新" in text for text in ctx_remove.replies)


@pytest.mark.asyncio
async def test_heartbeat_command_config_and_run(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    async def _allow(_ctx):
        return True

    async def _fake_run_now(user_id: str):
        return "HEARTBEAT_OK"

    monkeypatch.setattr(heartbeat_handlers, "check_permission_unified", _allow)
    monkeypatch.setattr(heartbeat_handlers.heartbeat_worker, "run_user_now", _fake_run_now)

    ctx_every = _DummyContext("u2", "/heartbeat every 45m")
    await heartbeat_handlers.heartbeat_command(ctx_every)
    assert any("45m" in text for text in ctx_every.replies)

    ctx_hours = _DummyContext("u2", "/heartbeat hours 09:00-21:00")
    await heartbeat_handlers.heartbeat_command(ctx_hours)
    assert any("09:00-21:00" in text for text in ctx_hours.replies)

    ctx_run = _DummyContext("u2", "/heartbeat run")
    await heartbeat_handlers.heartbeat_command(ctx_run)
    assert any("HEARTBEAT_OK" in text for text in ctx_run.replies)
