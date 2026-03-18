from types import SimpleNamespace

import pytest

import core.background_delivery as background_delivery_module
import core.scheduler as scheduler_module
from core.background_delivery import push_background_text


@pytest.mark.asyncio
async def test_push_background_text_uses_attachment_for_long_payload(monkeypatch):
    sent_documents: list[dict] = []
    sent_messages: list[dict] = []

    class _FakeAdapter:
        async def send_document(self, **kwargs):
            sent_documents.append(dict(kwargs))
            return SimpleNamespace(id="doc")

        async def send_message(self, **kwargs):
            sent_messages.append(dict(kwargs))
            return SimpleNamespace(id="msg")

    monkeypatch.setenv("BACKGROUND_PUSH_FILE_ENABLED", "true")
    monkeypatch.setenv("BACKGROUND_PUSH_FILE_THRESHOLD", "32")
    monkeypatch.setenv("BACKGROUND_PUSH_MAX_TEXT_CHUNKS", "1")

    ok = await push_background_text(
        platform="telegram",
        chat_id="c-1",
        text="这是一段很长的后台消息。" * 40,
        adapter=_FakeAdapter(),
        filename_prefix="rss",
    )

    assert ok is True
    assert sent_documents
    assert sent_documents[0]["filename"].startswith("rss-")
    assert not sent_messages


@pytest.mark.asyncio
async def test_scheduler_send_via_adapter_delegates_to_background_delivery(monkeypatch):
    calls: list[dict] = []

    async def fake_push_background_text(**kwargs):
        calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        scheduler_module,
        "push_background_text",
        fake_push_background_text,
    )

    await scheduler_module.send_via_adapter(
        chat_id="257675041",
        text="📢 RSS 更新",
        platform="telegram",
    )

    assert calls == [
        {
            "platform": "telegram",
            "chat_id": "257675041",
            "text": "📢 RSS 更新",
            "filename_prefix": "background",
        }
    ]


@pytest.mark.asyncio
async def test_push_background_text_records_history_to_bound_session(monkeypatch):
    saved: list[tuple[str, str, str, str]] = []

    class _FakeAdapter:
        async def send_message(self, **kwargs):
            return SimpleNamespace(id="msg", payload=dict(kwargs))

    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "c-1", "session_id": "sess-bound"}

    async def fake_get_latest_session_id(_user_id: str):
        raise AssertionError("latest session should not be used when delivery target is bound")

    async def fake_get_session_entries(_user_id: str, _session_id: str):
        return []

    async def fake_save_message(user_id: str, role: str, content: str, session_id: str):
        saved.append((user_id, role, content, session_id))
        return True

    monkeypatch.setattr(
        background_delivery_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "get_latest_session_id",
        fake_get_latest_session_id,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "get_session_entries",
        fake_get_session_entries,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "save_message",
        fake_save_message,
    )

    ok = await push_background_text(
        platform="telegram",
        chat_id="c-1",
        text="后台结果正文",
        adapter=_FakeAdapter(),
        record_history=True,
        history_user_id="u-history",
    )

    assert ok is True
    assert saved == [("u-history", "model", "后台结果正文", "sess-bound")]


@pytest.mark.asyncio
async def test_push_background_text_records_full_payload_for_attachment(monkeypatch):
    saved: list[tuple[str, str, str, str]] = []

    class _FakeAdapter:
        async def send_document(self, **kwargs):
            return SimpleNamespace(id="doc", payload=dict(kwargs))

    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "c-1", "session_id": ""}

    async def fake_get_latest_session_id(_user_id: str):
        return "sess-latest"

    async def fake_get_session_entries(_user_id: str, _session_id: str):
        return []

    async def fake_save_message(user_id: str, role: str, content: str, session_id: str):
        saved.append((user_id, role, content, session_id))
        return True

    monkeypatch.setenv("BACKGROUND_PUSH_FILE_ENABLED", "true")
    monkeypatch.setenv("BACKGROUND_PUSH_FILE_THRESHOLD", "32")
    monkeypatch.setenv("BACKGROUND_PUSH_MAX_TEXT_CHUNKS", "1")
    monkeypatch.setattr(
        background_delivery_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "get_latest_session_id",
        fake_get_latest_session_id,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "get_session_entries",
        fake_get_session_entries,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "save_message",
        fake_save_message,
    )

    payload = "这是一段很长的后台消息。" * 40
    ok = await push_background_text(
        platform="telegram",
        chat_id="c-1",
        text=payload,
        adapter=_FakeAdapter(),
        filename_prefix="rss",
        record_history=True,
        history_user_id="u-history",
    )

    assert ok is True
    assert saved == [("u-history", "model", payload, "sess-latest")]


@pytest.mark.asyncio
async def test_push_background_text_skips_duplicate_history_entry(monkeypatch):
    class _FakeAdapter:
        async def send_message(self, **kwargs):
            return SimpleNamespace(id="msg", payload=dict(kwargs))

    async def fake_get_delivery_target(_user_id: str):
        return {"platform": "telegram", "chat_id": "c-1", "session_id": "sess-bound"}

    async def fake_get_session_entries(_user_id: str, _session_id: str):
        return [{"role": "model", "content": "重复正文"}]

    async def fake_save_message(*_args, **_kwargs):
        raise AssertionError("duplicate content should not be appended")

    monkeypatch.setattr(
        background_delivery_module.heartbeat_store,
        "get_delivery_target",
        fake_get_delivery_target,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "get_session_entries",
        fake_get_session_entries,
    )
    monkeypatch.setattr(
        background_delivery_module,
        "save_message",
        fake_save_message,
    )

    ok = await push_background_text(
        platform="telegram",
        chat_id="c-1",
        text="重复正文",
        adapter=_FakeAdapter(),
        record_history=True,
        history_user_id="u-history",
    )

    assert ok is True
