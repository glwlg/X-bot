import os
import base64
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
    assert len(fake_client.chat.completions.calls) >= 1


@pytest.mark.asyncio
async def test_transcribe_voice_prefers_transcoded_wav_payload(monkeypatch):
    fake_client = _FakeClient([_Response(text="transcoded transcript")])
    monkeypatch.setattr(voice_handler, "openai_async_client", fake_client)

    async def _fake_transcode(voice_bytes: bytes, mime_type: str) -> bytes | None:
        assert voice_bytes.startswith(b"OggS")
        assert mime_type == "audio/ogg"
        return b"wav-payload"

    monkeypatch.setattr(voice_handler, "_transcode_audio_to_wav", _fake_transcode)

    result = await voice_handler.transcribe_voice(b"OggS-fake", "audio/ogg")

    assert result == "transcoded transcript"
    first_call = fake_client.chat.completions.calls[0]
    first_message = first_call["messages"][0]
    content_blocks = first_message.get("content", [])
    if isinstance(content_blocks, str):
        content_blocks = []
    has_wav = False
    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "file":
                if block["file"].get("filename") == "audio.wav":
                    has_wav = True
                    assert block["file"]["file_data"] == base64.b64encode(
                        b"wav-payload"
                    ).decode("utf-8")
            elif block.get("type") == "input_audio":
                if block["input_audio"].get("format") == "wav":
                    has_wav = True
                    assert block["input_audio"]["data"] == base64.b64encode(
                        b"wav-payload"
                    ).decode("utf-8")
    assert has_wav


@pytest.mark.asyncio
async def test_process_as_voice_message_falls_back_to_transcription(
    monkeypatch,
):
    captured: dict[str, str] = {}

    async def _fake_add_message(*args, **kwargs):
        return None

    async def _fake_get_user_context(*args, **kwargs):
        return []

    async def _fake_transcribe(*args, **kwargs):
        return "明天上午十点开会"

    async def _fake_process_as_text_message(ctx, text, thinking_msg):
        captured["text"] = text

    class _FakeAgent:
        async def handle_message(self, ctx, message_history):
            yield "主人，您这条消息里还没有附上语音文件/语音链接。"

    class _DummyCtx:
        def __init__(self):
            self.message = SimpleNamespace(user=SimpleNamespace(id="u-1"))
            self.edits = []

        async def edit_message(self, message_id, text, **kwargs):
            self.edits.append(str(text or ""))
            return None

    monkeypatch.setattr(voice_handler, "add_message", _fake_add_message)
    monkeypatch.setattr(voice_handler, "get_user_context", _fake_get_user_context)
    monkeypatch.setattr(voice_handler, "transcribe_voice", _fake_transcribe)
    monkeypatch.setattr(
        voice_handler,
        "process_as_text_message",
        _fake_process_as_text_message,
    )
    monkeypatch.setattr("core.agent_orchestrator.agent_orchestrator", _FakeAgent())

    ctx = _DummyCtx()
    thinking_msg = SimpleNamespace(message_id="m-1")

    await voice_handler.process_as_voice_message(
        ctx=ctx,
        voice_bytes=b"OggS-fake",
        mime_type="audio/ogg",
        user_instruction="帮我提醒",
        thinking_msg=thinking_msg,
    )

    assert "text" in captured
    assert "帮我提醒" in captured["text"]
    assert "明天上午十点开会" in captured["text"]
    assert any("正在处理" in item for item in ctx.edits)


def test_audio_missing_reply_detector_catches_model_phrase() -> None:
    text = "我现在还没有收到任何‘语音内容/音频文件’，因此无法提取意图。"
    assert voice_handler._looks_like_audio_missing_reply(text) is True


def test_normalize_transcribed_text_extracts_json_text_value() -> None:
    raw = 'json\n{"text":"明天上午十点开会"}'
    assert voice_handler._normalize_transcribed_text(raw) == "明天上午十点开会"
