from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging

import pytest

from core.state_file import parse_state_payload
from core.platform.models import Chat, UnifiedContext, UnifiedMessage, User
from core.platform.models import MessageType
from extension.channels.weixin.adapter import (
    WEIXIN_TYPING_STATUS_CANCEL,
    WEIXIN_TYPING_STATUS_TYPING,
    WeixinAdapter,
)


def _build_context(
    *,
    user_id: str = "wx-user-1",
    context_token: str = "ctx-1",
    account_id: str = "bot-1",
) -> UnifiedContext:
    message = UnifiedMessage(
        id="msg-1",
        platform="weixin",
        user=User(id=user_id, username=user_id, first_name=user_id),
        chat=Chat(id=user_id, type="private"),
        date=datetime.now(),
        type=MessageType.TEXT,
        text="hello",
        raw_data={
            "from_user_id": user_id,
            "to_user_id": account_id,
            "bot_account_id": account_id,
            "context_token": context_token,
        },
    )
    return UnifiedContext(
        message=message,
        platform_ctx=None,
        platform_event=message.raw_data,
        user=message.user,
    )


def _session(account_id: str) -> dict[str, str]:
    return {
        "token": f"token-{account_id}",
        "baseUrl": "https://ilinkai.weixin.qq.com/",
        "cdnBaseUrl": "https://novac2c.cdn.weixin.qq.com/c2c",
        "accountId": account_id,
    }


@pytest.mark.asyncio
async def test_send_chat_action_typing_fetches_ticket_and_sends_indicator():
    adapter = WeixinAdapter()
    adapter._apply_runtime_sessions({"bot-1": _session("bot-1")})
    calls: list[tuple[str, dict]] = []

    async def _fake_api_post(
        endpoint, payload, *, timeout, token=None, session_account_id=""
    ):
        _ = timeout
        _ = token
        assert session_account_id == "bot-1"
        calls.append((endpoint, dict(payload)))
        if endpoint == "ilink/bot/getconfig":
            return {"ret": 0, "typing_ticket": "ticket-1"}
        if endpoint == "ilink/bot/sendtyping":
            return {"ret": 0}
        raise AssertionError(endpoint)

    adapter._api_post = _fake_api_post  # type: ignore[method-assign]

    await adapter.send_chat_action(_build_context(), "typing")
    await adapter.stop()

    assert calls[0][0] == "ilink/bot/getconfig"
    assert calls[0][1]["ilink_user_id"] == "wx-user-1"
    assert calls[1][0] == "ilink/bot/sendtyping"
    assert calls[1][1]["typing_ticket"] == "ticket-1"
    assert calls[1][1]["status"] == WEIXIN_TYPING_STATUS_TYPING


@pytest.mark.asyncio
async def test_send_chat_action_reuses_cached_typing_ticket_and_can_cancel():
    adapter = WeixinAdapter()
    adapter._apply_runtime_sessions({"bot-1": _session("bot-1")})
    getconfig_calls = 0
    sendtyping_statuses: list[int] = []

    async def _fake_api_post(
        endpoint, payload, *, timeout, token=None, session_account_id=""
    ):
        nonlocal getconfig_calls
        _ = timeout
        _ = token
        assert session_account_id == "bot-1"
        if endpoint == "ilink/bot/getconfig":
            getconfig_calls += 1
            return {"ret": 0, "typing_ticket": "ticket-1"}
        if endpoint == "ilink/bot/sendtyping":
            sendtyping_statuses.append(int(payload["status"]))
            return {"ret": 0}
        raise AssertionError(endpoint)

    adapter._api_post = _fake_api_post  # type: ignore[method-assign]
    ctx = _build_context()

    await adapter.send_chat_action(ctx, "typing")
    await adapter.send_chat_action(ctx, "cancel_typing")
    await adapter.stop()

    assert getconfig_calls == 1
    assert sendtyping_statuses == [
        WEIXIN_TYPING_STATUS_TYPING,
        WEIXIN_TYPING_STATUS_CANCEL,
    ]


@pytest.mark.asyncio
async def test_persist_binding_writes_bindings_and_allow_list(tmp_path, monkeypatch):
    monkeypatch.setattr("extension.channels.weixin.adapter.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.config.DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    adapter = WeixinAdapter()
    result = await adapter._persist_binding(
        {
            "bot_token": "token-1",
            "baseurl": "https://ilinkai.weixin.qq.com/",
            "cdn_baseurl": "https://novac2c.cdn.weixin.qq.com/c2c",
            "ilink_bot_id": "bot-1",
            "ilink_user_id": "wx-user-9",
        },
        source="wxbind_qr",
        bound_by="admin-user",
    )

    bindings = json.loads((tmp_path / "weixin" / "bindings.json").read_text(encoding="utf-8"))
    allow_ok, allow_list = parse_state_payload(
        (tmp_path / "system" / "allowed_users.md").read_text(encoding="utf-8")
    )

    assert result["user_id"] == "wx-user-9"
    assert bindings["sessions"]["bot-1"]["token"] == "token-1"
    assert bindings["bound_users"]["wx-user-9"]["source"] == "wxbind_qr"
    assert bindings["bound_users"]["wx-user-9"]["account_id"] == "bot-1"
    assert allow_ok is True
    assert allow_list[0]["user_id"] == "wx-user-9"


@pytest.mark.asyncio
async def test_persist_binding_keeps_existing_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("extension.channels.weixin.adapter.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.config.DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    adapter = WeixinAdapter()
    await adapter._persist_binding(
        {
            "bot_token": "token-1",
            "baseurl": "https://ilinkai.weixin.qq.com/",
            "cdn_baseurl": "https://novac2c.cdn.weixin.qq.com/c2c",
            "ilink_bot_id": "bot-1",
            "ilink_user_id": "wx-user-1",
        },
        source="bootstrap_qr",
        bound_by="wx-user-1",
    )
    await adapter._persist_binding(
        {
            "bot_token": "token-2",
            "baseurl": "https://ilinkai.weixin.qq.com/",
            "cdn_baseurl": "https://novac2c.cdn.weixin.qq.com/c2c",
            "ilink_bot_id": "bot-2",
            "ilink_user_id": "wx-user-2",
        },
        source="wxbind_qr",
        bound_by="admin-user",
    )

    bindings = json.loads(
        (tmp_path / "weixin" / "bindings.json").read_text(encoding="utf-8")
    )

    assert bindings["version"] == 2
    assert bindings["sessions"]["bot-1"]["token"] == "token-1"
    assert bindings["sessions"]["bot-2"]["token"] == "token-2"
    assert bindings["bound_users"]["wx-user-1"]["account_id"] == "bot-1"
    assert bindings["bound_users"]["wx-user-2"]["account_id"] == "bot-2"


def test_render_qr_png_returns_png_bytes():
    payload = WeixinAdapter.render_qr_png("https://example.com/wxbind")

    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) > 100


@pytest.mark.asyncio
async def test_handle_incoming_message_accepts_nonstandard_user_link_message():
    adapter = WeixinAdapter()
    captured: dict[str, str] = {}

    async def _handler(ctx: UnifiedContext):
        captured["text"] = str(ctx.message.text or "")
        captured["type"] = ctx.message.type.value
        return None

    adapter.register_message_handler(_handler)

    await adapter._handle_incoming_message(
        {
            "from_user_id": "wx-user-10",
            "from_user_name": "Alice",
            "to_user_id": "bot-1",
            "message_type": 2,
            "client_id": "msg-link-1",
            "context_token": "ctx-link-1",
            "item_list": [
                {
                    "type": 6,
                    "link_item": {
                        "title": "GitHub",
                        "description": "A collective list of free APIs",
                        "url": "https://github.com/public-apis/public-apis",
                    },
                }
            ],
        }
    )

    assert captured["type"] == "text"
    assert "GitHub" in captured["text"]
    assert "https://github.com/public-apis/public-apis" in captured["text"]


@pytest.mark.asyncio
async def test_handle_incoming_message_skips_nonstandard_outbound_echo():
    adapter = WeixinAdapter()
    called = False

    async def _handler(ctx: UnifiedContext):
        nonlocal called
        called = True
        _ = ctx
        return None

    adapter.register_message_handler(_handler)

    await adapter._handle_incoming_message(
        {
            "from_user_id": "",
            "to_user_id": "wx-user-10",
            "message_type": 2,
            "client_id": "msg-out-1",
            "item_list": [
                {"type": 1, "text_item": {"text": "bot echo"}}
            ],
        }
    )

    assert called is False


@pytest.mark.asyncio
async def test_handle_incoming_message_skips_nonstandard_bot_self_message():
    adapter = WeixinAdapter()
    adapter._credentials = {"accountId": "bot-1"}
    called = False

    async def _handler(ctx: UnifiedContext):
        nonlocal called
        called = True
        _ = ctx
        return None

    adapter.register_message_handler(_handler)

    await adapter._handle_incoming_message(
        {
            "from_user_id": "bot-1",
            "to_user_id": "bot-1",
            "message_type": 2,
            "client_id": "msg-self-1",
            "item_list": [
                {
                    "type": 6,
                    "link_item": {
                        "title": "bot message",
                        "url": "https://example.com",
                    },
                }
            ],
        }
    )

    assert called is False


@pytest.mark.asyncio
async def test_poll_loop_persists_sync_buf_preferentially(monkeypatch):
    adapter = WeixinAdapter()
    saved: list[str] = []

    async def _fake_get_updates(cursor: str, *, account_id: str = ""):
        assert cursor == "cursor-start"
        assert account_id == "bot-1"
        adapter._stop_event.set()
        return {
            "ret": 0,
            "msgs": [],
            "sync_buf": "cursor-next",
            "get_updates_buf": "cursor-legacy",
        }

    monkeypatch.setattr(
        adapter, "_load_sync_cursor_for_account", lambda account_id: "cursor-start"
    )
    monkeypatch.setattr(
        adapter,
        "_save_sync_cursor_for_account",
        lambda account_id, cursor: saved.append(f"{account_id}:{cursor}"),
    )
    monkeypatch.setattr(adapter, "_get_updates", _fake_get_updates)

    await adapter._poll_loop("bot-1")

    assert saved == ["bot-1:cursor-next"]


@pytest.mark.asyncio
async def test_start_spawns_poll_task_per_session(monkeypatch):
    adapter = WeixinAdapter()
    adapter._apply_runtime_sessions(
        {
            "bot-1": _session("bot-1"),
            "bot-2": _session("bot-2"),
        }
    )
    started: list[str] = []
    release = asyncio.Event()

    async def _fake_ensure_client():
        return None

    async def _fake_ensure_credentials():
        return None

    async def _fake_poll_loop(account_id: str):
        started.append(account_id)
        await release.wait()

    monkeypatch.setattr(adapter, "_ensure_client", _fake_ensure_client)
    monkeypatch.setattr(adapter, "_ensure_credentials", _fake_ensure_credentials)
    monkeypatch.setattr(adapter, "_poll_loop", _fake_poll_loop)

    await adapter.start()
    await asyncio.sleep(0)

    assert sorted(adapter._poll_tasks) == ["bot-1", "bot-2"]
    assert sorted(started) == ["bot-1", "bot-2"]

    await adapter.stop()


@pytest.mark.asyncio
async def test_send_message_uses_scoped_context_token_and_session_account():
    adapter = WeixinAdapter()
    adapter._apply_runtime_sessions({"bot-2": _session("bot-2")})
    adapter._context_tokens["bot-2::wx-user-1"] = "ctx-2"
    calls: list[tuple[str, str, dict[str, object]]] = []

    async def _fake_api_post(
        endpoint, payload, *, timeout, token=None, session_account_id=""
    ):
        _ = timeout
        _ = token
        calls.append((endpoint, session_account_id, dict(payload)))
        return {"ret": 0}

    adapter._api_post = _fake_api_post  # type: ignore[method-assign]

    await adapter.send_message(
        "wx-user-1",
        "hello",
        session_account_id="bot-2",
    )

    assert len(calls) == 1
    endpoint, session_account_id, payload = calls[0]
    assert endpoint == "ilink/bot/sendmessage"
    assert session_account_id == "bot-2"
    assert payload["msg"]["to_user_id"] == "wx-user-1"
    assert payload["msg"]["context_token"] == "ctx-2"
    assert payload["msg"]["item_list"] == [{"type": 1, "text_item": {"text": "hello"}}]


def test_log_updates_summary_emits_payload_sample_when_enabled(caplog):
    adapter = WeixinAdapter()
    adapter.debug_updates = True

    with caplog.at_level(logging.INFO):
        adapter._log_updates_summary(
            {
                "msgs": [
                    {
                        "from_user_id": "wx-user-1",
                        "item_list": [{"type": 6}],
                    }
                ],
                "sync_buf": "cursor-next",
                "get_updates_buf": "cursor-legacy",
            }
        )

    assert "Weixin getupdates summary" in caplog.text
    assert "\"from_user_id\": \"wx-user-1\"" in caplog.text
