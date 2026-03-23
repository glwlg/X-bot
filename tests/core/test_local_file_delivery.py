from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.local_file_delivery import send_local_file


class _FakeCtx:
    def __init__(self, platform: str = "telegram"):
        self.message = SimpleNamespace(platform=platform)
        self.documents: list[dict[str, object]] = []
        self.photos: list[dict[str, object]] = []
        self.videos: list[dict[str, object]] = []
        self.audios: list[dict[str, object]] = []

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        self.documents.append(
            {
                "document": document,
                "filename": filename,
                "caption": caption,
                "kwargs": dict(kwargs),
            }
        )
        return SimpleNamespace(id="doc")

    async def reply_photo(self, photo, caption=None, **kwargs):
        self.photos.append(
            {
                "photo": photo,
                "caption": caption,
                "kwargs": dict(kwargs),
            }
        )
        return SimpleNamespace(id="photo")

    async def reply_video(self, video, caption=None, **kwargs):
        self.videos.append(
            {
                "video": video,
                "caption": caption,
                "kwargs": dict(kwargs),
            }
        )
        return SimpleNamespace(id="video")

    async def reply_audio(self, audio, caption=None, **kwargs):
        self.audios.append(
            {
                "audio": audio,
                "caption": caption,
                "kwargs": dict(kwargs),
            }
        )
        return SimpleNamespace(id="audio")


@pytest.mark.asyncio
async def test_send_local_file_sends_existing_relative_document(tmp_path):
    target = (tmp_path / "docs" / "report.txt").resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello", encoding="utf-8")
    ctx = _FakeCtx(platform="telegram")

    result = await send_local_file(
        ctx,
        path="docs/report.txt",
        caption="请查收",
        task_workspace_root=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["terminal"] is True
    assert ctx.documents
    assert ctx.documents[0]["document"] == str(target)
    assert ctx.documents[0]["filename"] == "report.txt"
    assert ctx.documents[0]["caption"] == "请查收"


@pytest.mark.asyncio
async def test_send_local_file_blocks_sensitive_env_file(tmp_path):
    sensitive = (tmp_path / ".env").resolve()
    sensitive.write_text("SECRET=1\n", encoding="utf-8")
    ctx = _FakeCtx(platform="telegram")

    result = await send_local_file(
        ctx,
        path=str(sensitive),
        task_workspace_root=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["terminal"] is True
    assert result["failure_mode"] == "fatal"
    assert "environment file blocked" in result["message"]
    assert ctx.documents == []


@pytest.mark.asyncio
async def test_send_local_file_falls_back_to_document_for_weixin_audio(
    tmp_path
):
    target = (tmp_path / "voice.mp3").resolve()
    target.write_bytes(b"fake-audio")
    ctx = _FakeCtx(platform="weixin")

    result = await send_local_file(
        ctx,
        path=str(target),
        task_workspace_root=str(tmp_path),
    )

    assert result["ok"] is True
    assert ctx.documents
    assert ctx.audios == []


@pytest.mark.asyncio
async def test_send_local_file_allows_readable_absolute_path_outside_workspace(tmp_path):
    workspace_root = (tmp_path / "workspace").resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    external = (tmp_path / "shared" / "baby_latest.jpg").resolve()
    external.parent.mkdir(parents=True, exist_ok=True)
    external.write_bytes(b"fake-image")
    ctx = _FakeCtx(platform="telegram")

    result = await send_local_file(
        ctx,
        path=str(external),
        task_workspace_root=str(workspace_root),
    )

    assert result["ok"] is True
    assert ctx.photos
    assert ctx.photos[0]["photo"] == str(external)
