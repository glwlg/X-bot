import sys
from types import SimpleNamespace

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

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )

    target = await scheduler_module._resolve_proactive_delivery_target(
        "user",
        "worker_runtime",
    )

    assert target == ("telegram", "257675041")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_prefers_explicit_metadata(monkeypatch):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "saved-chat"}

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )

    target = await scheduler_module._resolve_proactive_delivery_target(
        "257675041",
        "worker_runtime",
        metadata={
            "proactive_delivery_target": {
                "platform": "worker_runtime",
                "chat_id": "257675041",
                "user_id": "257675041",
            }
        },
    )

    assert target == ("telegram", "257675041")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_prefers_resource_binding_over_saved(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "saved-chat"}

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )

    target = await scheduler_module._resolve_proactive_delivery_target(
        "257675041",
        "telegram",
        metadata={
            "resource_binding": {
                "platform": "telegram",
                "chat_id": "resource-chat",
                "user_id": "257675041",
            }
        },
    )

    assert target == ("telegram", "resource-chat")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_returns_empty_without_binding(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "", "chat_id": ""}

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )

    target = await scheduler_module._resolve_proactive_delivery_target(
        "257675041",
        "telegram",
    )

    assert target == ("", "")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_rejects_shared_user_without_target(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "", "chat_id": ""}

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )

    target = await scheduler_module._resolve_proactive_delivery_target(
        "user",
        "telegram",
    )

    assert target == ("", "")


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_rejects_cross_user_metadata(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "saved-chat"}

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )

    with pytest.raises(ValueError, match="cross-user"):
        await scheduler_module._resolve_proactive_delivery_target(
            "1001",
            "telegram",
            metadata={
                "proactive_delivery_target": {
                    "platform": "telegram",
                    "chat_id": "2002",
                    "user_id": "1001",
                }
            },
        )


@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_rejects_metadata_without_owner_fields(
    monkeypatch,
):
    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "saved-chat"}

    monkeypatch.setattr(
        scheduler_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )

    with pytest.raises(ValueError, match="cross-user"):
        await scheduler_module._resolve_proactive_delivery_target(
            "1001",
            "telegram",
            metadata={
                "proactive_delivery_target": {
                    "platform": "telegram",
                    "chat_id": "2002",
                }
            },
        )


@pytest.mark.asyncio
async def test_send_feed_updates_passes_resource_metadata_to_target_resolution(
    monkeypatch,
):
    captured: dict[str, object] = {}

    async def fake_resolve(user_id, platform, metadata=None):
        captured["user_id"] = user_id
        captured["platform"] = platform
        captured["metadata"] = metadata
        return "telegram", "chat-1"

    async def fake_send_via_adapter(*, chat_id, text, platform, **kwargs):
        _ = (chat_id, text, platform, kwargs)

    async def fake_remember(user_id, platform, chat_id):
        _ = (user_id, platform, chat_id)

    async def fake_mark(updates):
        _ = updates

    monkeypatch.setattr(
        scheduler_module,
        "_resolve_proactive_delivery_target",
        fake_resolve,
    )
    monkeypatch.setattr(scheduler_module, "send_via_adapter", fake_send_via_adapter)
    monkeypatch.setattr(
        scheduler_module,
        "_remember_proactive_delivery_target",
        fake_remember,
    )
    monkeypatch.setattr(scheduler_module, "_mark_feed_updates_as_read", fake_mark)

    await scheduler_module._send_feed_updates(
        {
            ("telegram", "1001"): [
                {
                    "subscription_id": 7,
                    "user_id": "1001",
                    "platform": "telegram",
                    "resource_binding": {
                        "platform": "telegram",
                        "chat_id": "resource-chat",
                    },
                    "feed_title": "RSS",
                    "title": "Title",
                    "summary": "Summary",
                    "link": "https://example.com/item",
                    "last_entry_hash": "hash-1",
                    "last_etag": "etag-1",
                    "last_modified": "modified-1",
                }
            ]
        }
    )

    assert captured["user_id"] == "1001"
    assert captured["platform"] == "telegram"
    assert captured["metadata"] == {
        "subscription_id": 7,
        "user_id": "1001",
        "platform": "telegram",
        "resource_binding": {
            "platform": "telegram",
            "chat_id": "resource-chat",
        },
        "feed_title": "RSS",
        "title": "Title",
        "summary": "Summary",
        "link": "https://example.com/item",
        "last_entry_hash": "hash-1",
        "last_etag": "etag-1",
        "last_modified": "modified-1",
    }


@pytest.mark.asyncio
async def test_send_feed_updates_does_not_mark_read_on_send_failure(monkeypatch):
    marked: list[list[dict[str, object]]] = []

    async def fake_resolve(user_id, platform, metadata=None):
        _ = (user_id, platform, metadata)
        return "telegram", "chat-1"

    async def fake_send_via_adapter(*, chat_id, text, platform, **kwargs):
        _ = (chat_id, text, platform, kwargs)
        return False

    async def fake_remember(user_id, platform, chat_id):
        _ = (user_id, platform, chat_id)

    async def fake_mark(updates):
        marked.append(list(updates))

    monkeypatch.setattr(
        scheduler_module,
        "_resolve_proactive_delivery_target",
        fake_resolve,
    )
    monkeypatch.setattr(scheduler_module, "send_via_adapter", fake_send_via_adapter)
    monkeypatch.setattr(
        scheduler_module,
        "_remember_proactive_delivery_target",
        fake_remember,
    )
    monkeypatch.setattr(scheduler_module, "_mark_feed_updates_as_read", fake_mark)

    sent = await scheduler_module._send_feed_updates(
        {
            ("telegram", "1001"): [
                {
                    "subscription_id": 7,
                    "user_id": "1001",
                    "platform": "telegram",
                    "feed_title": "RSS",
                    "title": "Title",
                    "summary": "Summary",
                    "link": "https://example.com/item",
                    "last_entry_hash": "hash-1",
                    "last_etag": "etag-1",
                    "last_modified": "modified-1",
                }
            ]
        }
    )

    assert sent == 0
    assert marked == [[]]


@pytest.mark.asyncio
async def test_run_skill_cron_job_pushes_for_shared_user(monkeypatch):
    sent_messages: list[dict] = []

    class _FakeOrchestrator:
        async def handle_message(self, ctx, message_history):
            _ = (ctx, message_history)
            yield "执行完成"

    async def fake_send_via_adapter(*, chat_id, text, platform, **kwargs):
        sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "platform": platform,
                "kwargs": dict(kwargs),
            }
        )

    async def fake_resolve_proactive_delivery_target(user_id, platform):
        assert user_id == "user"
        assert platform == "telegram"
        return "telegram", "257675041"

    monkeypatch.setitem(
        sys.modules,
        "core.agent_orchestrator",
        SimpleNamespace(agent_orchestrator=_FakeOrchestrator()),
    )
    monkeypatch.setattr(
        scheduler_module,
        "send_via_adapter",
        fake_send_via_adapter,
    )
    monkeypatch.setattr(
        scheduler_module,
        "_resolve_proactive_delivery_target",
        fake_resolve_proactive_delivery_target,
    )

    await scheduler_module.run_skill_cron_job(
        "推送天气",
        user_id="user",
        platform="telegram",
        need_push=True,
    )

    assert sent_messages
    assert sent_messages[0]["chat_id"] == "257675041"
    assert sent_messages[0]["platform"] == "telegram"
    assert "定时任务执行报告" in sent_messages[0]["text"]
