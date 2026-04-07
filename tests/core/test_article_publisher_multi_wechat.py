import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "extension"
        / "skills"
        / "learned"
        / "article_publisher"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location(
        "article_publisher_multi_wechat_test",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _collect_chunks(result):
    chunks = []
    async for item in result:
        chunks.append(item)
    return chunks


async def _async_value(value):
    return value


def _stage_result(*, data=None, output_path="", files=None, ok=True, error="", failure_mode="recoverable"):
    return SimpleNamespace(
        ok=ok,
        data=data,
        output_path=output_path,
        files=files or {},
        error=error,
        failure_mode=failure_mode,
    )


@pytest.mark.asyncio
async def test_article_publisher_uses_selected_wechat_credential_for_publish(monkeypatch):
    module = _load_module()

    monkeypatch.setattr(
        module,
        "get_credential_entry",
        lambda _user_id, service, selector=None: _async_value(
            {
                "service": service,
                "id": "acc-b",
                "name": "副号",
                "data": {"app_id": "wx-b", "app_secret": "secret-b", "author": "副号作者"},
                "is_default": False,
            }
            if service == "wechat_official_account" and selector == "副号"
            else None
        ),
    )
    monkeypatch.setattr(module, "get_credential", lambda _user_id, _service: _async_value(None))

    async def fake_search_stage(*args, **kwargs):
        _ = (args, kwargs)
        return _stage_result(
            data={
                "topic": "测试主题",
                "context": "测试素材正文",
                "current_date": "",
            },
            output_path="/tmp/research.json",
        )

    async def fake_write_stage(*args, **kwargs):
        _ = (args, kwargs)
        return _stage_result(
            data={
                "title": "测试标题",
                "author": "模型作者",
                "digest": "测试摘要",
                "cover_prompt": None,
                "sections": [{"content": "<p>测试正文</p>", "image_prompt": None}],
            },
            output_path="/tmp/article.json",
        )

    async def fake_illustrate_stage(*args, **kwargs):
        _ = (args, kwargs)
        return _stage_result(
            data={"images": {"cover": "/tmp/cover.png"}},
            output_path="/tmp/article_with_images.json",
            files={"img_cover_-1.png": b"png"},
        )

    captured_accounts = {}

    async def fake_publish_stage(*args, **kwargs):
        captured_accounts.update(kwargs.get("accounts") or {})
        return _stage_result(
            data={"statuses": ["ok"]},
            output_path="/tmp/publish_result.json",
            files={},
        )

    monkeypatch.setattr(module, "search_stage", fake_search_stage)
    monkeypatch.setattr(module, "write_stage", fake_write_stage)
    monkeypatch.setattr(module, "illustrate_stage", fake_illustrate_stage)
    monkeypatch.setattr(module, "publish_stage", fake_publish_stage)

    ctx = SimpleNamespace(
        message=SimpleNamespace(
            text="",
            user=SimpleNamespace(id="user-1"),
        ),
        run_skill=lambda *args, **kwargs: _async_value({}),
    )

    chunks = await _collect_chunks(
        module.execute(
            ctx,
            {
                "topic": "把文章发布到副号公众号",
                "publish": True,
            },
            runtime=None,
        )
    )

    final = chunks[-1]
    assert final.get("ok") is True
    assert captured_accounts["wechat"]["app_id"] == "wx-b"
    assert captured_accounts["wechat"]["credential_name"] == "副号"
    assert captured_accounts["wechat"]["author"] == "副号作者"


@pytest.mark.asyncio
async def test_article_publisher_returns_error_when_selected_wechat_credential_missing(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "get_credential_entry", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(module, "get_credential", lambda *_args, **_kwargs: _async_value(None))

    ctx = SimpleNamespace(
        message=SimpleNamespace(
            text="",
            user=SimpleNamespace(id="user-1"),
        ),
        run_skill=lambda *args, **kwargs: _async_value({}),
    )

    chunks = await _collect_chunks(
        module.execute(
            ctx,
            {
                "topic": "发布到不存在的公众号",
                "publish": True,
                "wechat_account": "不存在",
            },
            runtime=None,
        )
    )

    final = chunks[-1]
    assert final.get("ok") is False
    assert final.get("failure_mode") == "recoverable"
    assert "未找到公众号凭据" in str(final.get("text") or "")


def test_article_publisher_does_not_treat_generic_wechat_phrase_as_account_name():
    module = _load_module()

    selector = module._resolve_wechat_account_selector({}, "文章写好之后发布到微信公众号")

    assert selector == ""
