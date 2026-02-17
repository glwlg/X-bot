import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_rss_execute_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "builtin"
        / "rss_subscribe"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location("rss_subscribe_execute_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_execute_slash_list_uses_list_action(monkeypatch):
    module = _load_rss_execute_module()

    called = {"list": 0, "subscribe": 0}

    async def fake_list_subs_command(ctx):
        _ = ctx
        called["list"] += 1
        return {"text": "LIST_OK", "ui": {}}

    async def fake_process_subscribe(ctx, url):
        _ = ctx
        called["subscribe"] += 1
        return {"text": f"ADD:{url}", "ui": {}}

    monkeypatch.setattr(module, "list_subs_command", fake_list_subs_command)
    monkeypatch.setattr(module, "process_subscribe", fake_process_subscribe)

    ctx = SimpleNamespace(
        message=SimpleNamespace(
            user=SimpleNamespace(id=1),
            platform="telegram",
            chat=SimpleNamespace(id=1),
        ),
        platform_ctx=None,
    )

    result = await module.execute(ctx, {"url": "/list"})

    assert result["text"] == "LIST_OK"
    assert called["list"] == 1
    assert called["subscribe"] == 0


@pytest.mark.asyncio
async def test_execute_list_alias_actions_use_list(monkeypatch):
    module = _load_rss_execute_module()

    called = {"list": 0, "subscribe": 0}

    async def fake_list_subs_command(ctx):
        _ = ctx
        called["list"] += 1
        return {"text": "LIST_OK", "ui": {}}

    async def fake_process_subscribe(ctx, url):
        _ = ctx
        called["subscribe"] += 1
        return {"text": f"ADD:{url}", "ui": {}}

    monkeypatch.setattr(module, "list_subs_command", fake_list_subs_command)
    monkeypatch.setattr(module, "process_subscribe", fake_process_subscribe)

    ctx = SimpleNamespace(
        message=SimpleNamespace(
            user=SimpleNamespace(id=1),
            platform="telegram",
            chat=SimpleNamespace(id=1),
        ),
        platform_ctx=None,
    )

    result_a = await module.execute(ctx, {"action": "list_subscriptions"})
    result_b = await module.execute(ctx, {"action": "list_all_feeds"})

    assert result_a["text"] == "LIST_OK"
    assert result_b["text"] == "LIST_OK"
    assert called["list"] == 2
    assert called["subscribe"] == 0


@pytest.mark.asyncio
async def test_execute_update_intent_overrides_list_alias_to_refresh(monkeypatch):
    module = _load_rss_execute_module()

    called = {"list": 0, "refresh": 0}

    async def fake_list_subs_command(ctx):
        _ = ctx
        called["list"] += 1
        return {"text": "LIST_OK", "ui": {}}

    async def fake_refresh_user_subscriptions(ctx):
        _ = ctx
        called["refresh"] += 1
        return "REFRESH_OK"

    monkeypatch.setattr(module, "list_subs_command", fake_list_subs_command)
    monkeypatch.setattr(
        module,
        "refresh_user_subscriptions",
        fake_refresh_user_subscriptions,
    )

    ctx = SimpleNamespace(
        message=SimpleNamespace(
            user=SimpleNamespace(id=1),
            platform="telegram",
            chat=SimpleNamespace(id=1),
            text="检查我的RSS订阅列表中有没有更新内容，有就推送给我",
        ),
        platform_ctx=None,
    )

    result = await module.execute(ctx, {"action": "list_subscriptions"})

    assert result["text"] == "REFRESH_OK"
    assert called["refresh"] == 1
    assert called["list"] == 0
