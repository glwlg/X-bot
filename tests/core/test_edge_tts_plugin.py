from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from core.reply_hooks import text_reply_hook_registry
from core.runtime_config_store import runtime_config_store
from extension.plugins.edge_tts import EdgeTtsPlugin, voiceout_command


class _FakeAdapter:
    platform_name = "telegram"

    def __init__(self) -> None:
        self.text_calls: list[str] = []
        self.audio_calls: list[bytes] = []
        self.voice_calls: list[bytes] = []

    async def reply_text(self, ctx: UnifiedContext, text: str, ui=None, **kwargs):
        _ = (ctx, ui, kwargs)
        self.text_calls.append(text)
        return SimpleNamespace(id=f"msg-{len(self.text_calls)}")

    async def edit_text(self, ctx: UnifiedContext, message_id: str, text: str, **kwargs):
        _ = (ctx, message_id, text, kwargs)
        return SimpleNamespace(id=message_id)

    async def reply_photo(self, ctx: UnifiedContext, photo, caption=None, **kwargs):
        _ = (ctx, photo, caption, kwargs)
        return None

    async def reply_video(self, ctx: UnifiedContext, video, caption=None, **kwargs):
        _ = (ctx, video, caption, kwargs)
        return None

    async def reply_document(
        self,
        ctx: UnifiedContext,
        document,
        filename=None,
        caption=None,
        **kwargs,
    ):
        _ = (ctx, document, filename, caption, kwargs)
        return None

    async def reply_audio(self, ctx: UnifiedContext, audio, caption=None, **kwargs):
        _ = (ctx, caption, kwargs)
        self.audio_calls.append(audio)
        return SimpleNamespace(id=f"audio-{len(self.audio_calls)}")

    async def reply_voice(self, ctx: UnifiedContext, voice, caption=None, **kwargs):
        _ = (ctx, caption, kwargs)
        self.voice_calls.append(voice)
        return SimpleNamespace(id=f"voice-{len(self.voice_calls)}")

    async def delete_message(self, ctx: UnifiedContext, message_id: str, chat_id=None, **kwargs):
        _ = (ctx, message_id, chat_id, kwargs)
        return None

    async def send_chat_action(self, ctx: UnifiedContext, action: str, chat_id=None, **kwargs):
        _ = (ctx, action, chat_id, kwargs)
        return None

    async def download_file(self, ctx: UnifiedContext, file_id: str, **kwargs):
        _ = (ctx, file_id, kwargs)
        return b""


def _build_context(adapter: _FakeAdapter, text: str = "/voiceout status") -> UnifiedContext:
    return UnifiedContext(
        message=UnifiedMessage(
            id="message-1",
            platform="telegram",
            user=User(id="u1", first_name="tester"),
            chat=Chat(id="c1", type="private"),
            date=datetime.now().astimezone(),
            type=MessageType.TEXT,
            text=text,
        ),
        platform_ctx=SimpleNamespace(args=text.split()[1:]),
        platform_event=None,
        _adapter=adapter,
        user=User(id="u1", first_name="tester"),
    )


@pytest.mark.asyncio
async def test_voiceout_command_toggles_runtime_config(tmp_path):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)

    adapter = _FakeAdapter()
    ctx = _build_context(adapter, "/voiceout on")

    await voiceout_command(ctx)

    assert runtime_config_store.is_voice_output_enabled(default=False) is True
    assert "已开启" in adapter.text_calls[-1]

    adapter.text_calls.clear()
    ctx = _build_context(adapter, "/voiceout off")
    await voiceout_command(ctx)

    assert runtime_config_store.is_voice_output_enabled(default=True) is False
    assert "已关闭" in adapter.text_calls[-1]


@pytest.mark.asyncio
async def test_edge_tts_plugin_emits_audio_after_text_reply(tmp_path, monkeypatch):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_store.set_voice_output_enabled(True, actor="test", reason="enable_test")

    async def _fake_speech(*args, **kwargs):
        _ = (args, kwargs)
        return b"edge-audio"

    monkeypatch.setattr(
        "extension.plugins.edge_tts.synthesize_edge_tts_speech",
        _fake_speech,
    )
    monkeypatch.setattr(
        "extension.plugins.edge_tts.transcode_audio_bytes_to_ogg_opus",
        lambda audio_bytes: _fake_speech(audio_bytes),
    )

    plugin = EdgeTtsPlugin()
    plugin.register(SimpleNamespace(register_command=lambda *args, **kwargs: None))

    adapter = _FakeAdapter()
    ctx = _build_context(adapter, "你好，今天的安排已经整理好了。")

    await ctx.reply("你好，今天的安排已经整理好了。")

    assert adapter.voice_calls == [b"edge-audio"]
    assert adapter.audio_calls == []


@pytest.mark.asyncio
async def test_edge_tts_plugin_dedupes_same_reply_hook_dispatch(tmp_path, monkeypatch):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_store.set_voice_output_enabled(True, actor="test", reason="enable_test")
    synthesize_calls = 0
    transcode_calls = 0

    async def _fake_speech(*args, **kwargs):
        nonlocal synthesize_calls
        _ = (args, kwargs)
        synthesize_calls += 1
        return b"edge-audio"

    async def _fake_transcode(*args, **kwargs):
        nonlocal transcode_calls
        _ = (args, kwargs)
        transcode_calls += 1
        return b"edge-voice"

    monkeypatch.setattr(
        "extension.plugins.edge_tts.synthesize_edge_tts_speech",
        _fake_speech,
    )
    monkeypatch.setattr(
        "extension.plugins.edge_tts.transcode_audio_bytes_to_ogg_opus",
        _fake_transcode,
    )

    plugin = EdgeTtsPlugin()
    plugin.register(SimpleNamespace(register_command=lambda *args, **kwargs: None))

    adapter = _FakeAdapter()
    ctx = _build_context(adapter, "重复语音测试")
    text = "你好，今天的安排已经整理好了。"

    await text_reply_hook_registry.dispatch_after_reply(ctx, text, object())
    await text_reply_hook_registry.dispatch_after_reply(ctx, text, object())

    assert synthesize_calls == 1
    assert transcode_calls == 1
    assert adapter.voice_calls == [b"edge-voice"]
    assert adapter.audio_calls == []


@pytest.mark.asyncio
async def test_edge_tts_plugin_skips_audio_fallback_after_voice_send_failure(
    tmp_path,
    monkeypatch,
):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_store.set_voice_output_enabled(True, actor="test", reason="enable_test")

    async def _fake_speech(*args, **kwargs):
        _ = (args, kwargs)
        return b"edge-audio"

    async def _raise_voice(*args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("Timed out")

    monkeypatch.setattr(
        "extension.plugins.edge_tts.synthesize_edge_tts_speech",
        _fake_speech,
    )
    monkeypatch.setattr(
        "extension.plugins.edge_tts.transcode_audio_bytes_to_ogg_opus",
        lambda audio_bytes: _fake_speech(audio_bytes),
    )

    plugin = EdgeTtsPlugin()
    plugin.register(SimpleNamespace(register_command=lambda *args, **kwargs: None))

    adapter = _FakeAdapter()
    adapter.reply_voice = _raise_voice
    ctx = _build_context(adapter, "发送失败测试")

    await ctx.reply("这段回复需要语音，但是 Telegram 语音发送返回超时。")

    assert adapter.voice_calls == []
    assert adapter.audio_calls == []


@pytest.mark.asyncio
async def test_edge_tts_plugin_skips_command_responses(tmp_path, monkeypatch):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_store.set_voice_output_enabled(True, actor="test", reason="enable_test")

    async def _fake_speech(*args, **kwargs):
        _ = (args, kwargs)
        return b"edge-audio"

    monkeypatch.setattr(
        "extension.plugins.edge_tts.synthesize_edge_tts_speech",
        _fake_speech,
    )
    monkeypatch.setattr(
        "extension.plugins.edge_tts.transcode_audio_bytes_to_ogg_opus",
        lambda audio_bytes: _fake_speech(audio_bytes),
    )

    plugin = EdgeTtsPlugin()
    plugin.register(SimpleNamespace(register_command=lambda *args, **kwargs: None))

    adapter = _FakeAdapter()
    ctx = _build_context(adapter, "/new")

    await ctx.reply("🧹 已开启新对话\n\n之前的短期对话上下文已清空。")

    assert adapter.voice_calls == []
    assert adapter.audio_calls == []


@pytest.mark.asyncio
async def test_edge_tts_plugin_skips_warning_prefixes(tmp_path, monkeypatch):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_store.set_voice_output_enabled(True, actor="test", reason="enable_test")

    async def _fake_speech(*args, **kwargs):
        _ = (args, kwargs)
        return b"edge-audio"

    monkeypatch.setattr(
        "extension.plugins.edge_tts.synthesize_edge_tts_speech",
        _fake_speech,
    )
    monkeypatch.setattr(
        "extension.plugins.edge_tts.transcode_audio_bytes_to_ogg_opus",
        lambda audio_bytes: _fake_speech(audio_bytes),
    )

    plugin = EdgeTtsPlugin()
    plugin.register(SimpleNamespace(register_command=lambda *args, **kwargs: None))

    adapter = _FakeAdapter()
    ctx = _build_context(adapter, "总结一下")

    await ctx.reply("⚠️ 不支持的文档格式。")
    await ctx.reply("🔇🔇🔇 这是一条静音提示。")

    assert adapter.voice_calls == []
    assert adapter.audio_calls == []


def test_plain_text_for_tts_strips_emoji():
    from extension.plugins.edge_tts import _plain_text_for_tts

    rendered = _plain_text_for_tts("你好，Master✨ 我是 Ikaros 👀🎉")

    assert rendered == "你好，Master 我是 伊卡洛斯"


@pytest.mark.asyncio
async def test_edge_tts_plugin_falls_back_to_audio_when_telegram_voice_transcode_fails(
    tmp_path,
    monkeypatch,
):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_store.set_voice_output_enabled(True, actor="test", reason="enable_test")

    async def _fake_speech(*args, **kwargs):
        _ = (args, kwargs)
        return b"edge-audio"

    async def _empty_transcode(*args, **kwargs):
        _ = (args, kwargs)
        return b""

    monkeypatch.setattr(
        "extension.plugins.edge_tts.synthesize_edge_tts_speech",
        _fake_speech,
    )
    monkeypatch.setattr(
        "extension.plugins.edge_tts.transcode_audio_bytes_to_ogg_opus",
        _empty_transcode,
    )

    plugin = EdgeTtsPlugin()
    plugin.register(SimpleNamespace(register_command=lambda *args, **kwargs: None))

    adapter = _FakeAdapter()
    ctx = _build_context(adapter, "你好")

    await ctx.reply("你好呀，Master✨ 我在。直接吩咐就好。")

    assert adapter.voice_calls == []
    assert adapter.audio_calls == [b"edge-audio"]


@pytest.mark.asyncio
async def test_edge_tts_plugin_runs_on_final_edit_message(tmp_path, monkeypatch):
    runtime_config_store.path = (tmp_path / "runtime-config.json").resolve()
    runtime_config_store.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_store.set_voice_output_enabled(True, actor="test", reason="enable_test")

    async def _fake_speech(*args, **kwargs):
        _ = (args, kwargs)
        return b"edge-audio"

    monkeypatch.setattr(
        "extension.plugins.edge_tts.synthesize_edge_tts_speech",
        _fake_speech,
    )
    monkeypatch.setattr(
        "extension.plugins.edge_tts.transcode_audio_bytes_to_ogg_opus",
        lambda audio_bytes: _fake_speech(audio_bytes),
    )

    plugin = EdgeTtsPlugin()
    plugin.register(SimpleNamespace(register_command=lambda *args, **kwargs: None))

    adapter = _FakeAdapter()
    ctx = _build_context(adapter, "你好")

    await ctx.edit_message(
        "thinking-1",
        "你好呀，Master✨ 我在。直接吩咐就好。",
        run_after_reply_hooks=True,
    )

    assert adapter.voice_calls == [b"edge-audio"]
