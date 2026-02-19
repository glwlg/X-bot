import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("LLM_API_KEY", "test-key")

import handlers.voice_handler as voice_handler


class _Response:
    def __init__(self, text=None, candidate_text: str = ""):
        self._text = text
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(
                    content=candidate_text,
                    tool_calls=[],
                )
            )
        ]

    @property
    def text(self):
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._responses:
            return self._responses.pop(0)
        return _Response(text="")


class _FakeChat:
    def __init__(self, responses):
        self.completions = _FakeModels(responses)


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(responses)


@pytest.mark.asyncio
async def test_transcribe_voice_extracts_candidate_text_when_response_text_raises(
    monkeypatch,
):
    fake_client = _FakeClient(
        [_Response(text=ValueError("no direct text"), candidate_text="测试语音内容")]
    )
    monkeypatch.setattr(voice_handler, "openai_async_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "audio/ogg")
    assert result == "测试语音内容"
    assert len(fake_client.chat.completions.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_voice_retries_with_fallback_mime(monkeypatch):
    fake_client = _FakeClient(
        [
            _Response(text=""),
            _Response(text="fallback transcript"),
        ]
    )
    monkeypatch.setattr(voice_handler, "openai_async_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "application/ogg")
    assert result == "fallback transcript"
    assert len(fake_client.chat.completions.calls) >= 2

    first_message = fake_client.chat.completions.calls[0]["messages"][0]
    second_message = fake_client.chat.completions.calls[1]["messages"][0]
    assert first_message["role"] == "user"
    assert second_message["role"] == "user"
    assert first_message != second_message


@pytest.mark.asyncio
async def test_transcribe_voice_skips_model_call_on_empty_audio(monkeypatch):
    fake_client = _FakeClient([_Response(text="should-not-be-used")])
    monkeypatch.setattr(voice_handler, "openai_async_client", fake_client)

    result = await voice_handler.transcribe_voice(b"", "audio/ogg")
    assert result is None
    assert fake_client.chat.completions.calls == []


@pytest.mark.asyncio
async def test_transcribe_and_translate_voice_parses_from_candidate_text(monkeypatch):
    fake_client = _FakeClient(
        [
            _Response(
                text=ValueError("no direct text"),
                candidate_text="原文语言：中文\n原文：今天天气不错\n译文：The weather is nice today",
            )
        ]
    )
    monkeypatch.setattr(voice_handler, "openai_async_client", fake_client)

    result = await voice_handler.transcribe_and_translate_voice(
        b"audio-bytes", "audio/ogg"
    )
    assert result is not None
    assert result["original_lang"] == "中文"
    assert result["original"] == "今天天气不错"
    assert result["translated"] == "The weather is nice today"


@pytest.mark.asyncio
async def test_transcribe_voice_strips_wrapping_quotes(monkeypatch):
    fake_client = _FakeClient([_Response(text=' "你好" ')])
    monkeypatch.setattr(voice_handler, "openai_async_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "audio/ogg")
    assert result == "你好"


@pytest.mark.asyncio
async def test_transcribe_voice_retries_when_first_result_is_quote_placeholder(
    monkeypatch,
):
    fake_client = _FakeClient(
        [
            _Response(text='""""'),
            _Response(text="你好"),
        ]
    )
    monkeypatch.setattr(voice_handler, "openai_async_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "audio/ogg")
    assert result == "你好"
    assert len(fake_client.chat.completions.calls) >= 2
