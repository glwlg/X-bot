import pytest

import core.scheduler as scheduler_module
from core.scheduler import _resolve_entry_link


def test_resolve_entry_link_prefers_primary_link():
    entry = {
        "link": "https://news.example.com/story/42?from=rss",
        "summary": (
            '<a href="https://news.example.com/story/43?from=rss">新闻标题</a>'
            '<font color="#6f6f6f">via Example</font>'
        ),
    }

    resolved = _resolve_entry_link(entry, fallback_url="https://fallback.example.com")
    assert resolved == "https://news.example.com/story/42?from=rss"


def test_resolve_entry_link_falls_back_when_no_primary_link():
    entry = {
        "link": "",
        "summary": "no links here",
    }

    resolved = _resolve_entry_link(entry, fallback_url="https://fallback.example.com")
    assert resolved == "https://fallback.example.com"


@pytest.mark.asyncio
async def test_trigger_manual_rss_check_busy_message_can_be_suppressed():
    await scheduler_module._rss_check_lock.acquire()
    try:
        visible = await scheduler_module.trigger_manual_rss_check(1)
        hidden = await scheduler_module.trigger_manual_rss_check(
            1,
            suppress_busy_message=True,
        )
    finally:
        scheduler_module._rss_check_lock.release()

    assert visible == "⚠️ 正在进行定时更新检查，请稍后再试。"
    assert hidden == ""


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_prefers_saved_target(monkeypatch):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "257675041"}

    class _Queue:
        async def list_tasks(self, limit: int = 50):
            _ = limit
            return []

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(scheduler_module, "dispatch_queue", _Queue())

    target = await scheduler_module._resolve_proactive_delivery_target(
        "user",
        "worker_runtime",
    )

    assert target == ("telegram", "257675041")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_uses_recent_task_fallback(monkeypatch):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "", "chat_id": ""}

    class _Task:
        def __init__(self, metadata):
            self.metadata = metadata

    class _Queue:
        async def list_tasks(self, limit: int = 50):
            _ = limit
            return [
                _Task(
                    {
                        "platform": "telegram",
                        "user_id": "user",
                        "chat_id": "1089191264244736050",
                        "session_id": "hb-1773024281",
                    }
                ),
                _Task(
                    {
                        "platform": "telegram",
                        "user_id": "0",
                        "chat_id": "0",
                        "session_id": "1773015000",
                    }
                ),
                _Task(
                    {
                        "platform": "telegram",
                        "user_id": "257675041",
                        "chat_id": "257675041",
                        "session_id": "f1a20603123b",
                    }
                ),
            ]

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(scheduler_module, "dispatch_queue", _Queue())

    target = await scheduler_module._resolve_proactive_delivery_target(
        "user",
        "telegram",
    )

    assert target == ("telegram", "257675041")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_shared_user_prefers_recent_over_saved(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "1089191264244736050"}

    class _Task:
        def __init__(self, metadata):
            self.metadata = metadata

    class _Queue:
        async def list_tasks(self, limit: int = 50):
            _ = limit
            return [
                _Task(
                    {
                        "platform": "telegram",
                        "user_id": "257675041",
                        "chat_id": "257675041",
                        "session_id": "f1a20603123b",
                    }
                ),
            ]

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(scheduler_module, "dispatch_queue", _Queue())

    target = await scheduler_module._resolve_proactive_delivery_target(
        "user",
        "telegram",
    )

    assert target == ("telegram", "257675041")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_falls_back_to_numeric_user_id(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "", "chat_id": ""}

    class _Queue:
        async def list_tasks(self, limit: int = 50):
            _ = limit
            return []

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(scheduler_module, "dispatch_queue", _Queue())

    target = await scheduler_module._resolve_proactive_delivery_target(
        "257675041",
        "telegram",
    )

    assert target == ("telegram", "257675041")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_rejects_shared_user_without_target(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "", "chat_id": ""}

    class _Queue:
        async def list_tasks(self, limit: int = 50):
            _ = limit
            return [
                _Task(
                    {
                        "platform": "telegram",
                        "user_id": "user",
                        "chat_id": "1089191264244736050",
                        "session_id": "hb-1773024281",
                    }
                )
            ]

    class _Task:
        def __init__(self, metadata):
            self.metadata = metadata

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(scheduler_module, "dispatch_queue", _Queue())

    target = await scheduler_module._resolve_proactive_delivery_target(
        "user",
        "telegram",
    )

    assert target == ("", "")
