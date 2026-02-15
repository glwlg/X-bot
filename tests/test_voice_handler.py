import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import handlers.voice_handler as voice_handler


class _Part:
    def __init__(self, text: str = ""):
        self.text = text


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Candidate:
    def __init__(self, content):
        self.content = content


class _Response:
    def __init__(self, text=None, candidate_text: str = ""):
        self._text = text
        self.candidates = [_Candidate(_Content([_Part(candidate_text)]))]

    @property
    def text(self):
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._responses:
            return self._responses.pop(0)
        return _Response(text="")


@pytest.mark.asyncio
async def test_transcribe_voice_extracts_candidate_text_when_response_text_raises(monkeypatch):
    fake_models = _FakeModels(
        [_Response(text=ValueError("no direct text"), candidate_text="测试语音内容")]
    )
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr(voice_handler, "gemini_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "audio/ogg")
    assert result == "测试语音内容"
    assert len(fake_models.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_voice_retries_with_fallback_mime(monkeypatch):
    fake_models = _FakeModels(
        [
            _Response(text=""),
            _Response(text="fallback transcript"),
        ]
    )
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr(voice_handler, "gemini_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "application/ogg")
    assert result == "fallback transcript"
    assert len(fake_models.calls) >= 2

    first_mime = fake_models.calls[0]["contents"][0]["parts"][1]["inline_data"][
        "mime_type"
    ]
    first_role = fake_models.calls[0]["contents"][0]["role"]
    second_mime = fake_models.calls[1]["contents"][0]["parts"][1]["inline_data"][
        "mime_type"
    ]
    assert first_mime == "application/ogg"
    assert first_role == "user"
    assert second_mime != first_mime


@pytest.mark.asyncio
async def test_transcribe_voice_skips_model_call_on_empty_audio(monkeypatch):
    fake_models = _FakeModels([_Response(text="should-not-be-used")])
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr(voice_handler, "gemini_client", fake_client)

    result = await voice_handler.transcribe_voice(b"", "audio/ogg")
    assert result is None
    assert fake_models.calls == []


@pytest.mark.asyncio
async def test_transcribe_and_translate_voice_parses_from_candidate_text(monkeypatch):
    fake_models = _FakeModels(
        [
            _Response(
                text=ValueError("no direct text"),
                candidate_text="原文语言：中文\n原文：今天天气不错\n译文：The weather is nice today",
            )
        ]
    )
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr(voice_handler, "gemini_client", fake_client)

    result = await voice_handler.transcribe_and_translate_voice(
        b"audio-bytes", "audio/ogg"
    )
    assert result is not None
    assert result["original_lang"] == "中文"
    assert result["original"] == "今天天气不错"
    assert result["translated"] == "The weather is nice today"


@pytest.mark.asyncio
async def test_transcribe_voice_strips_wrapping_quotes(monkeypatch):
    fake_models = _FakeModels([_Response(text=' "你好" ')])
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr(voice_handler, "gemini_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "audio/ogg")
    assert result == "你好"


@pytest.mark.asyncio
async def test_transcribe_voice_retries_when_first_result_is_quote_placeholder(monkeypatch):
    fake_models = _FakeModels(
        [
            _Response(text='""""'),
            _Response(text="你好"),
        ]
    )
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr(voice_handler, "gemini_client", fake_client)

    result = await voice_handler.transcribe_voice(b"audio-bytes", "audio/ogg")
    assert result == "你好"
    assert len(fake_models.calls) >= 2
