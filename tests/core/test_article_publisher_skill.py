import importlib.util
import json
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
        "article_publisher_execute_test",
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
async def test_article_publisher_returns_recoverable_error_without_topic():
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
async def test_article_publisher_calls_web_search_with_expected_params(monkeypatch):
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
                "author": "Ikaros",
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
    assert str(final.get("text") or "").startswith("🔇🔇🔇【文章内容】")


@pytest.mark.asyncio
async def test_article_publisher_account_author_overrides_model_author(monkeypatch):
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
        "get_credential",
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
async def test_article_publisher_uses_local_material_without_search(monkeypatch, tmp_path: Path):
    module = _load_module()
    material_path = (tmp_path / "video_text.md").resolve()
    material_path.write_text(
        "# 视频文本工件\n\n## 音轨转写\n\n今天我们讲如何构建提示词工程流程。",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "get_current_model", lambda: "demo/current-model")
    monkeypatch.setattr(
        module,
        "get_client_for_model",
        lambda model_name, is_async=True: (
            object() if model_name == "demo/current-model" and is_async else None
        ),
    )

    captured_prompts: list[str] = []
    skill_calls: list[tuple[str, dict]] = []
    fetched_urls: list[str] = []

    async def fake_generate_text(async_client, model, contents, config=None):
        _ = (async_client, model, config)
        captured_prompts.append(str(contents))
        return json.dumps(
            {
                "title": "本地素材文章",
                "author": "Ikaros",
                "digest": "本地素材摘要",
                "cover_prompt": "clean workspace illustration",
                "sections": [
                    {"content": "<p>教程正文</p>", "image_prompt": "desk and notes"},
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
            if skill_name == "generate_image":
                return {"files": {"demo.png": b"png"}}
            raise AssertionError(f"unexpected skill call: {skill_name}")

    chunks = await _collect_chunks(
        module.execute(
            _FakeCtx(),
            {
                "topic": "基于素材写一篇教程",
                "source_path": str(material_path),
            },
            runtime=None,
        )
    )

    assert skill_calls
    assert all(name == "generate_image" for name, _params in skill_calls)
    assert fetched_urls == []
    assert captured_prompts
    assert str(material_path) in captured_prompts[0]
    assert "今天我们讲如何构建提示词工程流程" in captured_prompts[0]
    final = chunks[-1]
    assert final.get("ok") is True
    assert "img_cover_-1.png" in (final.get("files") or {})


@pytest.mark.asyncio
async def test_article_publisher_returns_recoverable_error_for_missing_local_material():
    module = _load_module()
    ctx = SimpleNamespace(
        message=SimpleNamespace(
            text="",
            user=SimpleNamespace(id="user-1"),
        )
    )

    chunks = await _collect_chunks(
        module.execute(
            ctx,
            {"source_path": "/tmp/not-found-video-text.md"},
            runtime=None,
        )
    )

    final = chunks[-1]
    assert final.get("ok") is False
    assert final.get("failure_mode") == "recoverable"
    assert "本地素材读取失败" in str(final.get("text") or "")


@pytest.mark.asyncio
async def test_article_publisher_generates_xiaohongshu_draft_files(
    monkeypatch,
):
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
        "get_credential",
        lambda _user_id, account_type: _async_value(
            {"endpoint": "https://publisher.example.com/xhs", "author": "炜煜"}
            if account_type == "xiaohongshu_publisher"
            else None
        ),
    )

    captured_prompts: list[str] = []

    async def fake_generate_text(async_client, model, contents, config=None):
        _ = (async_client, model, config)
        captured_prompts.append(str(contents))
        prompt = str(contents)
        if "小红书编辑" in prompt:
            return json.dumps(
                {
                    "title": "提示词工程怎么落地",
                    "body": "先拆任务，再收上下文，最后固化成可复用流程。",
                    "tags": ["提示词工程", "AI工作流", "效率工具"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "title": "本地素材文章",
                "author": "Ikaros",
                "digest": "本地素材摘要",
                "cover_prompt": "clean workspace illustration",
                "sections": [
                    {"content": "<p>教程正文</p>", "image_prompt": "desk and notes"},
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
            if skill_name == "generate_image":
                return {"files": {"demo.png": b"png"}}
            raise AssertionError(f"unexpected skill call: {skill_name}")

    chunks = await _collect_chunks(
        module.execute(
            _FakeCtx(),
            {"topic": "提示词工程", "publish_channel": "xiaohongshu"},
            runtime=None,
        )
    )

    final = chunks[-1]
    assert final.get("ok") is True
    files = final.get("files") or {}
    assert "xiaohongshu_note.txt" in files
    assert "xiaohongshu_note.json" in files
    assert "提示词工程怎么落地" in files["xiaohongshu_note.txt"].decode("utf-8")
    assert "已生成小红书发布草稿附件" in str(final.get("text") or "")
    assert any("小红书编辑" in prompt for prompt in captured_prompts)


@pytest.mark.asyncio
async def test_article_publisher_publish_preflight_fails_before_generation(
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


@pytest.mark.asyncio
async def test_article_publisher_xiaohongshu_publish_preflight_fails_before_generation(
    monkeypatch,
):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_prepare_xiaohongshu_opencli",
        lambda: _async_value(
            "⚠️ 发布中止：未找到 `opencli` 命令，请先安装并确保它在 PATH 中。"
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
            {
                "topic": "OpenAI",
                "publish": True,
                "publish_channel": "xiaohongshu",
            },
            runtime=None,
        )
    )

    assert len(chunks) == 2
    assert "检查 opencli 小红书发布能力" in str(chunks[0])
    final = chunks[-1]
    assert final.get("ok") is False
    assert final.get("failure_mode") == "fatal"
    assert "PATH" in str(final.get("text") or "")


async def _async_tuple(first, second):
    return first, second


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_article_publisher_image_prompt_uses_author_watermark():
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


def test_article_publisher_account_author_overrides_generated_author():
    module = _load_module()

    author = module._resolve_article_author(
        {"app_id": "x", "app_secret": "y", "author": "测试号"},
        fallback_author="模型作者",
    )

    assert author == "测试号"


def test_article_publisher_fallback_author_generates_watermark():
    module = _load_module()

    author = module._resolve_article_author(None, fallback_author="模型作者")

    assert author == "模型作者"
    assert module._author_watermark(author) == "@模型作者"


def test_article_publisher_params_from_args_include_source_paths():
    module = _load_module()
    parser = module._build_parser()

    args = parser.parse_args(
        ["--source-path", "/tmp/a.md", "--source-path", "/tmp/b.txt", "写一篇教程"]
    )
    params = module._params_from_args(args)

    assert params["topic"] == "写一篇教程"
    assert params["source_paths"] == ["/tmp/a.md", "/tmp/b.txt"]


def test_article_publisher_params_from_args_include_publish_channels():
    module = _load_module()
    parser = module._build_parser()

    args = parser.parse_args(
        [
            "--publish",
            "--publish-channel",
            "wechat",
            "--publish-channel",
            "xiaohongshu",
            "写一篇教程",
        ]
    )
    params = module._params_from_args(args)

    assert params["publish"] is True
    assert params["publish_channels"] == ["wechat", "xiaohongshu"]
