from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.platform.models import MessageType
from handlers import ai_handlers
from handlers import voice_handler


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
        self.reactions = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return _DummyOutgoingMessage(len(self.replies))

    async def edit_message(self, message_id, text, **kwargs):
        self.edits.append((message_id, text, kwargs))
        return True

    async def send_chat_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return True

    async def set_message_reaction(self, emoji, **kwargs):
        self.reactions.append((emoji, kwargs))
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

    async def _fake_handle_message(_ctx, _history):
        yield "分析结果"

    monkeypatch.setattr(config_module, "is_user_allowed", _allow_user)
    monkeypatch.setattr(ai_handlers, "add_message", AsyncMock())
    monkeypatch.setattr(ai_handlers, "increment_stat", AsyncMock())
    monkeypatch.setattr(ai_handlers, "get_user_context", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        ai_handlers,
        "process_and_send_code_files",
        AsyncMock(return_value="分析结果"),
    )
    monkeypatch.setattr(
        "core.agent_orchestrator.agent_orchestrator.handle_message",
        _fake_handle_message,
    )
    monkeypatch.setattr(
        "core.task_manager.task_manager.register_task",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "core.task_manager.task_manager.is_cancelled", lambda _user_id: False
    )
    monkeypatch.setattr(
        "core.task_manager.task_manager.unregister_task",
        lambda _user_id: None,
    )

    message = _build_discord_message(MessageType.IMAGE, "image/png")
    platform_event = SimpleNamespace(
        attachments=[SimpleNamespace(id="att-1", content_type="image/png", size=1234)]
    )
    ctx = _DummyContext(message, platform_event)

    await ai_handlers.handle_ai_photo(ctx)

    assert ctx.download_calls
    assert ctx.reactions == [("👀", {})]
    assert any("分析结果" in text for _, text, _ in ctx.edits)


@pytest.mark.asyncio
async def test_handle_ai_video_works_for_discord_without_telegram_update(monkeypatch):
    import core.config as config_module

    async def _allow_user(_user_id):
        return True

    monkeypatch.setattr(config_module, "is_user_allowed", _allow_user)
    monkeypatch.setattr(ai_handlers, "add_message", AsyncMock())
    monkeypatch.setattr(ai_handlers, "increment_stat", AsyncMock())
    monkeypatch.setattr(ai_handlers, "get_vision_model", lambda: "vision-model")
    monkeypatch.setattr(ai_handlers, "get_current_model", lambda: "fallback-model")
    monkeypatch.setattr(
        ai_handlers, "get_client_for_model", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        ai_handlers, "generate_text", AsyncMock(return_value="分析结果")
    )
    monkeypatch.setattr(
        ai_handlers.prompt_composer, "compose_base", lambda **_kwargs: "system prompt"
    )

    message = _build_discord_message(MessageType.VIDEO, "video/mp4")
    platform_event = SimpleNamespace(
        attachments=[SimpleNamespace(id="att-1", content_type="video/mp4", size=1234)]
    )
    ctx = _DummyContext(message, platform_event)

    await ai_handlers.handle_ai_video(ctx)

    assert ctx.download_calls
    assert ctx.reactions == [("👀", {})]
    assert any("分析结果" in text for _, text, _ in ctx.edits)


@pytest.mark.asyncio
async def test_process_as_voice_message_uses_weixin_embedded_transcript_fallback(
    monkeypatch,
):
    message = _build_discord_message(MessageType.VOICE, "audio/silk")
    message.platform = "weixin"
    message.raw_data = {
        "item_list": [
            {
                "type": 3,
                "voice_item": {
                    "text": "这是微信侧已经转写好的内容",
                },
            }
        ]
    }
    ctx = _DummyContext(message, platform_event=None)
    thinking_msg = _DummyOutgoingMessage(1)

    monkeypatch.setattr(voice_handler, "transcribe_voice", AsyncMock(return_value=None))
    process_text = AsyncMock()
    monkeypatch.setattr(voice_handler, "process_as_text_message", process_text)

    await voice_handler.process_as_voice_message(
        ctx=ctx,
        voice_bytes=b"fake-silk",
        mime_type="audio/silk",
        user_instruction=None,
        thinking_msg=thinking_msg,
    )

    process_text.assert_awaited_once()
    args = process_text.await_args.args
    assert args[1] == "这是微信侧已经转写好的内容"
    assert any("语音已识别" in text for _, text, _ in ctx.edits)
