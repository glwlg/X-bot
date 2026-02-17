from types import SimpleNamespace

import pytest

from platforms.telegram.adapter import TelegramAdapter


class _FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(id="tg-1")


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
