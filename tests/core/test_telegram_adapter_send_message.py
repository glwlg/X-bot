from types import SimpleNamespace

import pytest

from core.platform.exceptions import MessageSendError
from platforms.telegram.adapter import TelegramAdapter


class _FakeBot:
    def __init__(self):
        self.calls = []
        self.draft_calls = []
        self.photo_calls = []
        self.video_calls = []
        self.audio_calls = []

    async def send_message(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(id="tg-1")

    async def send_message_draft(self, **kwargs):
        self.draft_calls.append(dict(kwargs))
        return True

    async def send_photo(self, **kwargs):
        self.photo_calls.append(dict(kwargs))
        return SimpleNamespace(id="tg-photo")

    async def send_video(self, **kwargs):
        self.video_calls.append(dict(kwargs))
        return SimpleNamespace(id="tg-video")

    async def send_audio(self, **kwargs):
        self.audio_calls.append(dict(kwargs))
        return SimpleNamespace(id="tg-audio")


@pytest.mark.asyncio
async def test_telegram_adapter_send_message_renders_markdown_link():
    fake_bot = _FakeBot()
    app = SimpleNamespace(bot=fake_bot)
    adapter = TelegramAdapter(app)

    await adapter.send_message(
        chat_id=100,
        text="- [查看详情](https://example.com/rss/item)",
    )

    assert fake_bot.calls
    payload = fake_bot.calls[-1]
    assert payload["chat_id"] == 100
    assert payload["parse_mode"] == "HTML"
    assert payload["disable_web_page_preview"] is True
    assert '<a href="https://example.com/rss/item">查看详情</a>' in payload["text"]


@pytest.mark.asyncio
async def test_telegram_adapter_send_message_retries_timeout(monkeypatch):
    class _FlakyBot:
        def __init__(self):
            self.calls = 0

        async def send_message(self, **kwargs):
            _ = kwargs
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError("timed out")
            return SimpleNamespace(id="tg-ok")

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr("platforms.telegram.adapter.asyncio.sleep", _fast_sleep)

    flaky_bot = _FlakyBot()
    app = SimpleNamespace(bot=flaky_bot)
    adapter = TelegramAdapter(app)
    result = await adapter.send_message(chat_id=100, text="hello")

    assert result.id == "tg-ok"
    assert flaky_bot.calls == 3


@pytest.mark.asyncio
async def test_telegram_adapter_send_message_draft_renders_markdown_link():
    fake_bot = _FakeBot()
    app = SimpleNamespace(bot=fake_bot)
    adapter = TelegramAdapter(app)

    result = await adapter.send_message_draft(
        chat_id="100",
        draft_id=42,
        text="- [查看详情](https://example.com/rss/item)",
    )

    assert result is True
    assert fake_bot.draft_calls
    payload = fake_bot.draft_calls[-1]
    assert payload["chat_id"] == 100
    assert payload["draft_id"] == 42
    assert payload["parse_mode"] == "HTML"
    assert '<a href="https://example.com/rss/item">查看详情</a>' in payload["text"]


@pytest.mark.asyncio
async def test_telegram_adapter_send_message_draft_falls_back_when_api_missing():
    class _LegacyBot:
        def __init__(self):
            self.calls = []

        async def send_message(self, **kwargs):
            self.calls.append(dict(kwargs))
            return SimpleNamespace(id="tg-fallback")

    app = SimpleNamespace(bot=_LegacyBot())
    adapter = TelegramAdapter(app)
    result = await adapter.send_message_draft(
        chat_id=100,
        draft_id=7,
        text="处理中",
    )

    assert result.id == "tg-fallback"
    assert app.bot.calls
    assert app.bot.calls[-1]["chat_id"] == 100


@pytest.mark.asyncio
async def test_telegram_adapter_send_message_draft_can_raise_without_message_fallback():
    class _LegacyBot:
        async def send_message(self, **kwargs):
            _ = kwargs
            return SimpleNamespace(id="unused")

    app = SimpleNamespace(bot=_LegacyBot())
    adapter = TelegramAdapter(app)

    with pytest.raises(MessageSendError):
        await adapter.send_message_draft(
            chat_id=100,
            draft_id=7,
            text="处理中",
            fallback_to_message=False,
        )


@pytest.mark.asyncio
async def test_telegram_adapter_send_photo_uses_bot_photo_api():
    fake_bot = _FakeBot()
    app = SimpleNamespace(bot=fake_bot)
    adapter = TelegramAdapter(app)

    result = await adapter.send_photo(chat_id="100", photo="/tmp/demo.png", caption="完成")

    assert result.id == "tg-photo"
    assert fake_bot.photo_calls
    payload = fake_bot.photo_calls[-1]
    assert payload["chat_id"] == 100
    assert payload["photo"] == "/tmp/demo.png"
    assert payload["parse_mode"] == "HTML"
