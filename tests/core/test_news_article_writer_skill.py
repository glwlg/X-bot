import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "learned"
        / "news_article_writer"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location(
        "news_article_writer_execute_test",
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


class _ImageCtx:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run_skill(self, skill_name: str, params: dict):
        self.calls.append((skill_name, dict(params)))
        if skill_name == "generate_image":
            return {"files": {"demo.png": b"png"}}
        raise AssertionError(f"unexpected skill call: {skill_name}")


@pytest.mark.asyncio
async def test_news_article_writer_returns_recoverable_error_without_topic():
    module = _load_module()
    ctx = SimpleNamespace(
        message=SimpleNamespace(
            text="",
            user=SimpleNamespace(id="user-1"),
        )
    )

    chunks = await _collect_chunks(module.execute(ctx, {}, runtime=None))

    assert chunks
    final = chunks[-1]
    assert final.get("ok") is False
    assert final.get("failure_mode") == "recoverable"
    assert "请提供文章主题" in str(final.get("text") or "")


@pytest.mark.asyncio
async def test_news_article_writer_calls_web_search_with_expected_params(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "get_current_model", lambda: "demo/current-model")
    monkeypatch.setattr(
        module,
        "get_client_for_model",
        lambda model_name, is_async=True: (
            object() if model_name == "demo/current-model" and is_async else None
        ),
    )

    captured_prompts: list[str] = []
    fetched_urls: list[str] = []
    skill_calls: list[tuple[str, dict]] = []

    async def fake_generate_text(async_client, model, contents, config=None):
        _ = (async_client, model, config)
        captured_prompts.append(str(contents))
        return json.dumps(
            {
                "title": "测试标题",
                "author": "X-Bot",
                "digest": "测试摘要",
                "cover_prompt": None,
                "sections": [
                    {"content": "<p>测试正文</p>", "image_prompt": None},
                ],
            },
            ensure_ascii=False,
        )

    async def fake_fetch_webpage_content(url: str):
        fetched_urls.append(url)
        return f"网页内容 {url}"

    monkeypatch.setattr(module, "generate_text", fake_generate_text)
    monkeypatch.setattr(module, "fetch_webpage_content", fake_fetch_webpage_content)

    class _FakeCtx:
        def __init__(self):
            self.message = SimpleNamespace(
                text="",
                user=SimpleNamespace(id="user-1"),
            )

        async def run_skill(self, skill_name: str, params: dict):
            skill_calls.append((skill_name, dict(params)))
            if skill_name == "web_search":
                return {
                    "text": "",
                    "files": {
                        "search_report.md": (
                            b"# Search Report\n"
                            b"### [Source A](https://example.com/a)\n"
                            b"### [Source B](https://example.com/b)\n"
                        )
                    },
                }
            raise AssertionError(f"unexpected skill call: {skill_name}")

    chunks = await _collect_chunks(
        module.execute(_FakeCtx(), {"topic": "OpenAI"}, runtime=None)
    )

    assert skill_calls == [("web_search", {"query": "OpenAI", "num_results": 8})]
    assert fetched_urls == ["https://example.com/a", "https://example.com/b"]
    assert captured_prompts
    assert "Src: https://example.com/a" in captured_prompts[0]

    final = chunks[-1]
    assert final.get("ok") is True
    assert str(final.get("text") or "").startswith("🔇🔇🔇【新闻文章】")


@pytest.mark.asyncio
async def test_news_article_writer_account_author_overrides_model_author(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "get_current_model", lambda: "demo/current-model")
    monkeypatch.setattr(
        module,
        "get_client_for_model",
        lambda model_name, is_async=True: (
            object() if model_name == "demo/current-model" and is_async else None
        ),
    )
    monkeypatch.setattr(
        module,
        "get_account",
        lambda _user_id, _account_type: _async_value(
            {"app_id": "x", "app_secret": "y", "author": "炜煜"}
        ),
    )

    async def fake_generate_text(async_client, model, contents, config=None):
        _ = (async_client, model, contents, config)
        return json.dumps(
            {
                "title": "测试标题",
                "author": "深响科技局",
                "digest": "测试摘要",
                "cover_prompt": None,
                "sections": [
                    {"content": "<p>测试正文</p>", "image_prompt": None},
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(module, "generate_text", fake_generate_text)
    monkeypatch.setattr(
        module,
        "fetch_webpage_content",
        lambda _url: _async_value("网页内容"),
    )

    class _FakeCtx:
        def __init__(self):
            self.message = SimpleNamespace(
                text="",
                user=SimpleNamespace(id="user-1"),
            )

        async def run_skill(self, skill_name: str, params: dict):
            if skill_name == "web_search":
                return {"text": "https://example.com/a", "files": {}}
            raise AssertionError(f"unexpected skill call: {skill_name}")

    chunks = await _collect_chunks(
        module.execute(_FakeCtx(), {"topic": "OpenAI"}, runtime=None)
    )

    final = chunks[-1]
    assert final.get("ok") is True
    assert "*By 炜煜*" in str(final.get("text") or "")
    assert "深响科技局" not in str(final.get("text") or "")


@pytest.mark.asyncio
async def test_news_article_writer_publish_preflight_fails_before_generation(
    monkeypatch,
):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_prepare_wechat_publisher",
        lambda ctx: _async_tuple(
            None,
            "❌ 发布前检查失败：当前服务器出口 IP 不在微信公众号白名单中：`112.21.16.207`。",
        ),
    )

    class _FakeCtx:
        def __init__(self):
            self.message = SimpleNamespace(
                text="",
                user=SimpleNamespace(id="user-1"),
            )

        async def run_skill(self, skill_name: str, params: dict):
            raise AssertionError(f"unexpected skill call: {skill_name} {params}")

    chunks = await _collect_chunks(
        module.execute(
            _FakeCtx(),
            {"topic": "OpenAI", "publish": True},
            runtime=None,
        )
    )

    assert len(chunks) == 2
    assert "检查公众号发布权限" in str(chunks[0])
    final = chunks[-1]
    assert final.get("ok") is False
    assert final.get("failure_mode") == "fatal"
    assert "IP 不在微信公众号白名单中" in str(final.get("text") or "")


async def _async_tuple(first, second):
    return first, second


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_news_article_writer_image_prompt_uses_author_watermark():
    module = _load_module()
    ctx = _ImageCtx()

    await module._generate_images(
        ctx,
        {
            "cover_prompt": "red lobster",
            "sections": [],
        },
        author="炜煜",
    )

    assert ctx.calls
    _, params = ctx.calls[0]
    prompt = str(params.get("prompt") or "")
    assert "@炜煜" in prompt
    assert "no extra watermark" in prompt


def test_news_article_writer_account_author_overrides_generated_author():
    module = _load_module()

    author = module._resolve_article_author(
        {"app_id": "x", "app_secret": "y", "author": "测试号"},
        fallback_author="模型作者",
    )

    assert author == "测试号"


def test_news_article_writer_fallback_author_generates_watermark():
    module = _load_module()

    author = module._resolve_article_author(None, fallback_author="模型作者")

    assert author == "模型作者"
    assert module._author_watermark(author) == "@模型作者"
