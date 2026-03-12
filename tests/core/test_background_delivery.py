from types import SimpleNamespace

import pytest

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
