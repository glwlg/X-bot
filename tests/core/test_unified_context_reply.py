from datetime import datetime

import pytest

from core.platform.models import (
    Chat,
    MAX_EDIT_PREVIEW_CHARS,
    MAX_REPLY_TEXT_CHARS,
    MessageType,
    UnifiedContext,
    UnifiedMessage,
    User,
)
from core.platform.exceptions import MessageSendError


class DummyAdapter:
    def __init__(self):
        self.calls = []
        self.edit_calls = []
        self.fail_first_too_long = False
        self.fail_first_timeout = False

    async def reply_text(self, context, text, ui=None, **kwargs):
        self.calls.append({"text": text, "ui": ui, "kwargs": kwargs})
        return {"message_id": len(self.calls)}

    async def edit_text(self, context, message_id, text, **kwargs):
        self.edit_calls.append(
            {"message_id": message_id, "text": text, "kwargs": kwargs}
        )
        if self.fail_first_too_long and len(self.edit_calls) == 1:
            raise MessageSendError("Message_too_long")
        if self.fail_first_timeout and len(self.edit_calls) == 1:
            raise MessageSendError("Timed out")
        return {"message_id": message_id, "text": text}


def _build_context(adapter: DummyAdapter) -> UnifiedContext:
    msg = UnifiedMessage(
        id="m1",
        platform="telegram",
        user=User(id="u1"),
        chat=Chat(id="c1", type="private"),
        date=datetime.now(),
        type=MessageType.TEXT,
        text="hi",
    )
    return UnifiedContext(message=msg, platform_ctx=None, _adapter=adapter)


@pytest.mark.asyncio
async def test_reply_splits_long_text_without_document_conversion():
    adapter = DummyAdapter()
    ctx = _build_context(adapter)

    long_text = "A " * (MAX_REPLY_TEXT_CHARS + 1200)
    result = await ctx.reply(long_text, reply_markup={"k": "v"})

    assert len(adapter.calls) >= 2
    assert all(len(call["text"]) <= MAX_REPLY_TEXT_CHARS for call in adapter.calls)
    assert adapter.calls[0]["kwargs"].get("reply_markup") == {"k": "v"}
    assert "reply_markup" not in adapter.calls[1]["kwargs"]
    assert result == {"message_id": len(adapter.calls)}


@pytest.mark.asyncio
async def test_edit_message_truncates_preview_before_adapter_call():
    adapter = DummyAdapter()
    ctx = _build_context(adapter)

    long_text = "B" * (MAX_EDIT_PREVIEW_CHARS + 900)
    await ctx.edit_message("mid-1", long_text)

    assert len(adapter.edit_calls) == 1
    assert len(adapter.edit_calls[0]["text"]) <= MAX_EDIT_PREVIEW_CHARS
    assert "完整结果将在后续消息中给出" in adapter.edit_calls[0]["text"]


@pytest.mark.asyncio
async def test_edit_message_retries_with_shorter_text_on_too_long_error():
    adapter = DummyAdapter()
    adapter.fail_first_too_long = True
    ctx = _build_context(adapter)

    long_text = "C" * (MAX_EDIT_PREVIEW_CHARS + 2000)
    await ctx.edit_message("mid-2", long_text)

    assert len(adapter.edit_calls) == 2
    assert len(adapter.edit_calls[1]["text"]) < len(adapter.edit_calls[0]["text"])


@pytest.mark.asyncio
async def test_reply_coerces_non_string_payload():
    adapter = DummyAdapter()
    ctx = _build_context(adapter)

    await ctx.reply(-1)

    assert len(adapter.calls) == 1
    assert adapter.calls[0]["text"] == "-1"


@pytest.mark.asyncio
async def test_edit_message_falls_back_to_reply_on_timeout_error():
    adapter = DummyAdapter()
    adapter.fail_first_timeout = True
    ctx = _build_context(adapter)

    result = await ctx.edit_message("mid-timeout", "timeout fallback")

    assert len(adapter.edit_calls) == 1
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["text"] == "timeout fallback"
    assert result == {"message_id": 1}
