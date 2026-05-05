import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class _ProgressMessage:
    id = "progress-message"
    message_id = "progress-message"


class _FakeContext:
    platform_ctx = object()

    def __init__(self, tmp_path: Path):
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id="chat-1"),
            user=SimpleNamespace(id="user-1"),
            text="/download https://example.com/video",
        )
        self.user_data = {}
        self.replies: list[str] = []
        self.deleted: list[str] = []
        self.sent_audio: list[object] = []
        self.sent_video: list[object] = []
        self.tmp_path = tmp_path

    async def reply(self, text, **_kwargs):
        self.replies.append(str(text))
        return _ProgressMessage()

    async def edit_message(self, *_args, **_kwargs):
        return None

    async def delete_message(self, message_id):
        self.deleted.append(str(message_id))

    async def reply_audio(self, *args, **kwargs):
        self.sent_audio.append((args, kwargs))

    async def reply_video(self, *args, **kwargs):
        self.sent_video.append((args, kwargs))


def _load_download_module(module_name: str):
    root = Path(__file__).resolve().parents[2]
    script_dir = root / "extension/skills/builtin/download_video/scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "execute.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_download_video_returns_file_payload_without_direct_send(monkeypatch, tmp_path):
    module = _load_download_module("download_video_delivery_test")
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"fake video")

    async def fake_download_video(url, user_id, progress_message, audio_only=False):
        assert url == "https://example.com/video"
        assert user_id == "chat-1"
        assert progress_message.id == "progress-message"
        assert audio_only is False
        return SimpleNamespace(
            success=True,
            error_message="",
            file_path=str(media_path),
            is_too_large=False,
            file_size_mb=1.0,
        )

    async def fake_increment_stat(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module, "download_video", fake_download_video)
    monkeypatch.setattr("stats.increment_stat", fake_increment_stat)

    ctx = _FakeContext(tmp_path)
    result = await module.process_video_download(ctx, "https://example.com/video")

    assert ctx.sent_audio == []
    assert ctx.sent_video == []
    assert ctx.deleted == ["progress-message"]
    assert result["text"] == "✅ 视频下载完成。"
    assert result["files"] == [
        {
            "path": str(media_path),
            "filename": "clip.mp4",
            "kind": "video",
            "caption": "",
        }
    ]
