from __future__ import annotations

from types import SimpleNamespace

import pytest

from handlers.base_handlers import edit_callback_message


class _FakeContext:
    def __init__(self, *, edit_result=None):
        self.message = SimpleNamespace(id="msg-1")
        self.edit_result = edit_result
        self.callback_texts: list[str | None] = []

    async def edit_message(self, message_id: str, text: str, **kwargs):
        return self.edit_result

    async def answer_callback(self, text: str = None, **kwargs):
        self.callback_texts.append(text)


@pytest.mark.asyncio
async def test_edit_callback_message_shows_notice_when_message_not_modified():
    ctx = _FakeContext(edit_result=None)

    await edit_callback_message(ctx, "same content")

    assert ctx.callback_texts == ["当前已是该页面"]


@pytest.mark.asyncio
async def test_edit_callback_message_sends_silent_ack_when_message_changes():
    ctx = _FakeContext(edit_result=SimpleNamespace(id="edited"))

    await edit_callback_message(ctx, "new content")

    assert ctx.callback_texts == [None]
