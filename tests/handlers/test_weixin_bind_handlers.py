from __future__ import annotations

from types import SimpleNamespace

import pytest

from handlers.weixin_bind_handlers import weixin_bind_command


class _DummyAdapter:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, str]] = []
        self.render_calls: list[str] = []

    async def start_additional_binding(
        self,
        *,
        requester_user_id: str,
        requester_account_id: str = "",
        notification_platform: str = "",
        notification_chat_id: str = "",
    ) -> dict[str, str]:
        self.start_calls.append(
            {
                "requester_user_id": str(requester_user_id),
                "requester_account_id": str(requester_account_id),
                "notification_platform": str(notification_platform),
                "notification_chat_id": str(notification_chat_id),
            }
        )
        return {
            "qrcode_token": "qr-token-1",
            "qr_content": "https://wx.example/qr/abc",
        }

    def render_qr_png(self, data: str) -> bytes:
        self.render_calls.append(str(data))
        return f"png::{data}".encode("utf-8")


class _DummyContext:
    def __init__(self, *, platform: str = "weixin", chat_id: str = "admin-chat") -> None:
        raw_data = {"to_user_id": "bot-1"} if platform == "weixin" else {}
        self.message = SimpleNamespace(
            user=SimpleNamespace(id="admin-user"),
            platform=platform,
            text="/wxbind qr",
            chat=SimpleNamespace(id=chat_id),
            raw_data=raw_data,
        )
        self.reply_calls: list[dict[str, object]] = []
        self.reply_photo_calls: list[dict[str, object]] = []

    async def reply(self, text: str, **kwargs) -> None:
        self.reply_calls.append({"text": text, "kwargs": dict(kwargs)})

    async def reply_photo(self, photo, caption=None, **kwargs) -> None:
        self.reply_photo_calls.append(
            {
                "photo": photo,
                "caption": caption,
                "kwargs": dict(kwargs),
            }
        )


@pytest.mark.asyncio
async def test_wxbind_qr_replies_with_rendered_png(monkeypatch):
    adapter = _DummyAdapter()
    monkeypatch.setattr(
        "handlers.weixin_bind_handlers.is_user_admin",
        lambda user_id: True,
    )
    monkeypatch.setattr(
        "handlers.weixin_bind_handlers.adapter_manager.get_adapter",
        lambda platform_name: adapter,
    )
    ctx = _DummyContext()

    await weixin_bind_command(ctx)

    assert adapter.start_calls == [
        {
            "requester_user_id": "admin-user",
            "requester_account_id": "bot-1",
            "notification_platform": "weixin",
            "notification_chat_id": "admin-chat",
        }
    ]
    assert adapter.render_calls == ["https://wx.example/qr/abc"]
    assert len(ctx.reply_photo_calls) == 1
    assert ctx.reply_photo_calls[0]["photo"] == b"png::https://wx.example/qr/abc"
    assert ctx.reply_photo_calls[0]["kwargs"]["filename"] == "weixin-bind-qr.png"
    assert ctx.reply_calls == []


@pytest.mark.asyncio
async def test_wxbind_qr_can_be_triggered_from_telegram(monkeypatch):
    adapter = _DummyAdapter()
    monkeypatch.setattr(
        "handlers.weixin_bind_handlers.is_user_admin",
        lambda user_id: True,
    )
    monkeypatch.setattr(
        "handlers.weixin_bind_handlers.adapter_manager.get_adapter",
        lambda platform_name: adapter,
    )
    ctx = _DummyContext(platform="telegram", chat_id="123456")

    await weixin_bind_command(ctx)

    assert adapter.start_calls == [
        {
            "requester_user_id": "admin-user",
            "requester_account_id": "",
            "notification_platform": "telegram",
            "notification_chat_id": "123456",
        }
    ]
    assert len(ctx.reply_photo_calls) == 1
    assert ctx.reply_calls == []
