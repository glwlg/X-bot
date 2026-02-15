from types import SimpleNamespace

import pytest

from core.platform.models import MessageType
from handlers import ai_handlers


class _FakeGeminiResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeGeminiModels:
    async def generate_content(self, **kwargs):
        return _FakeGeminiResponse("分析结果")


class _FakeGeminiAio:
    def __init__(self):
        self.models = _FakeGeminiModels()


class _FakeGeminiClient:
    def __init__(self):
        self.aio = _FakeGeminiAio()


class _DummyOutgoingMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id
        self.id = message_id

    async def delete(self):
        return True


class _DummyContext:
    def __init__(self, message, platform_event):
        self.message = message
        self.platform_event = platform_event
        self.platform_ctx = None
        self._adapter = SimpleNamespace(can_update_message=True)
        self.replies = []
        self.edits = []
        self.actions = []
        self.download_calls = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return _DummyOutgoingMessage(len(self.replies))

    async def edit_message(self, message_id, text, **kwargs):
        self.edits.append((message_id, text, kwargs))
        return True

    async def send_chat_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return True

    async def download_file(self, file_id, **kwargs):
        self.download_calls.append((file_id, kwargs))
        return b"fake-media-bytes"


def _build_discord_message(msg_type: MessageType, content_type: str):
    return SimpleNamespace(
        id="m1",
        platform="discord",
        type=msg_type,
        text="",
        caption="",
        file_id="att-1",
        file_url=None,
        file_name="demo.bin",
        file_size=1234,
        mime_type=content_type,
        width=100,
        height=100,
        duration=5,
        raw_data={},
        reply_to_message=None,
        user=SimpleNamespace(id="u1"),
        chat=SimpleNamespace(id="c1"),
    )


@pytest.mark.asyncio
async def test_handle_ai_photo_works_for_discord_without_telegram_update(monkeypatch):
    import core.config as config_module

    async def _allow_user(_user_id):
        return True

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(config_module, "is_user_allowed", _allow_user)
    monkeypatch.setattr(ai_handlers, "add_message", _noop)
    monkeypatch.setattr(ai_handlers, "increment_stat", _noop)
    monkeypatch.setattr(ai_handlers, "gemini_client", _FakeGeminiClient())

    message = _build_discord_message(MessageType.IMAGE, "image/png")
    platform_event = SimpleNamespace(
        attachments=[SimpleNamespace(id="att-1", content_type="image/png", size=1234)]
    )
    ctx = _DummyContext(message, platform_event)

    await ai_handlers.handle_ai_photo(ctx)

    assert ctx.download_calls
    assert any("分析结果" in text for _, text, _ in ctx.edits)


@pytest.mark.asyncio
async def test_handle_ai_video_works_for_discord_without_telegram_update(monkeypatch):
    import core.config as config_module

    async def _allow_user(_user_id):
        return True

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(config_module, "is_user_allowed", _allow_user)
    monkeypatch.setattr(ai_handlers, "add_message", _noop)
    monkeypatch.setattr(ai_handlers, "increment_stat", _noop)
    monkeypatch.setattr(ai_handlers, "gemini_client", _FakeGeminiClient())

    message = _build_discord_message(MessageType.VIDEO, "video/mp4")
    platform_event = SimpleNamespace(
        attachments=[SimpleNamespace(id="att-1", content_type="video/mp4", size=1234)]
    )
    ctx = _DummyContext(message, platform_event)

    await ai_handlers.handle_ai_video(ctx)

    assert ctx.download_calls
    assert any("分析结果" in text for _, text, _ in ctx.edits)
