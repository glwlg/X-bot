import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = (
    REPO_ROOT
    / "extension"
    / "skills"
    / "learned"
    / "article_publisher"
    / "scripts"
)

RAW_TOPIC = (
    "用daily_query获取今天日期，然后用article_publisher技能写一篇关于中国最新人工智能周边新闻的公众号文章，"
    "确保新闻是当天的新闻，至少配图三张，配图要符合文章主题，注意文章内容不要包含非正文的内容，"
    "注意不要用涉及华为的内容，这是一篇直接面向公众读者的文章，写好之后发布到公众号。注意不要用子任务"
)


def _load_module(module_name: str, relative_path: str):
    for path in (REPO_ROOT, REPO_ROOT / "src", SKILL_SCRIPTS):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)

    target = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, target)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _collect_chunks(result):
    chunks = []
    async for item in result:
        chunks.append(item)
    return chunks


class _SearchCtx:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run_skill(self, skill_name: str, params: dict):
        self.calls.append((skill_name, dict(params)))
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


@pytest.mark.asyncio
async def test_search_stage_uses_same_day_news_query_and_filters_forbidden_terms(
    monkeypatch,
    tmp_path: Path,
):
    module = _load_module(
        "article_publisher_search_public_facing_test",
        "extension/skills/learned/article_publisher/scripts/ap_stages/search.py",
    )
    ctx = _SearchCtx()

    async def fake_fetch_webpage_content(url: str):
        if url.endswith("/a"):
            return "今日中国 AI 公司发布新进展，聚焦智能体与机器人落地。"
        return "华为发布相关人工智能消息。"

    monkeypatch.setattr(module, "fetch_webpage_content", fake_fetch_webpage_content)

    result = await module.search_stage(
        ctx,
        topic=RAW_TOPIC,
        params={},
        output_dir=str(tmp_path),
        current_date="2026-04-07",
    )

    assert result.ok is True
    assert ctx.calls == [
        (
            "web_search",
            {
                "query": "中国最新人工智能周边新闻 2026-04-07",
                "num_results": 8,
                "categories": "news",
                "time_range": "day",
            },
        )
    ]
    assert "华为" not in result.data["context"]
    assert "智能体与机器人落地" in result.data["context"]
    validation = result.data["news_validation"]
    assert validation["candidate_source_count"] == 2
    assert validation["has_enough_news"] is True
    assert validation["suspected_same_day"] is True
    assert validation["recommend_reject"] is False


@pytest.mark.asyncio
async def test_search_stage_returns_structured_reject_for_insufficient_news(
    monkeypatch,
    tmp_path: Path,
):
    module = _load_module(
        "article_publisher_search_reject_public_facing_test",
        "extension/skills/learned/article_publisher/scripts/ap_stages/search.py",
    )

    class _InsufficientSearchCtx:
        async def run_skill(self, skill_name: str, params: dict):
            if skill_name == "web_search":
                return {
                    "text": "",
                    "files": {
                        "search_report.md": (
                            b"# Search Report\n"
                            b"### [Source Only](https://example.com/only)\n"
                        )
                    },
                }
            raise AssertionError(f"unexpected skill call: {skill_name}")

    async def fake_fetch_webpage_content(_url: str):
        return "这是行业背景回顾，没有给出当天新增事实。"

    monkeypatch.setattr(module, "fetch_webpage_content", fake_fetch_webpage_content)

    result = await module.search_stage(
        _InsufficientSearchCtx(),
        topic=RAW_TOPIC,
        params={},
        output_dir=str(tmp_path),
        current_date="2026-04-07",
    )

    assert result.ok is True
    validation = result.data["news_validation"]
    assert validation["is_news_request"] is True
    assert validation["candidate_source_count"] == 1
    assert validation["suspected_same_day"] is False
    assert validation["has_enough_news"] is False
    assert validation["recommend_reject"] is True
    assert validation["reject_reasons"]
    assert "不建议发文" in validation["reject_message"]


@pytest.mark.asyncio
async def test_write_prompt_includes_public_reader_guardrails(monkeypatch):
    module = _load_module(
        "article_publisher_write_public_facing_test",
        "extension/skills/learned/article_publisher/scripts/ap_stages/write.py",
    )

    monkeypatch.setattr(module, "select_model_for_role", lambda _role: "demo-model")
    monkeypatch.setattr(module, "get_client_for_model", lambda _model, is_async=True: object())

    captured: dict[str, str] = {}

    async def fake_generate_text(async_client, model, contents, config=None):
        _ = (async_client, model, config)
        captured["prompt"] = str(contents)
        return json.dumps(
            {
                "title": "今日中国 AI 新闻速览",
                "author": "测试作者",
                "digest": "测试摘要",
                "cover_prompt": "editorial illustration for china ai news",
                "sections": [
                    {
                        "content": "<h2>今日看点</h2><p>"
                        + ("正文内容" * 80)
                        + "</p><p style=\"margin-bottom:1.5em;\"></p>",
                        "image_prompt": "newsroom with robotics and ai screens",
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(module, "generate_text", fake_generate_text)

    result = await module._generate_article_json(
        RAW_TOPIC,
        "Src: https://example.com/a\n2026-04-07 中国 AI 新闻摘要。",
        900,
        current_date="2026-04-07",
    )

    assert result["title"] == "今日中国 AI 新闻速览"
    prompt = captured["prompt"]
    assert "主题「中国最新人工智能周边新闻」" in prompt
    assert "公众号读者" in prompt
    assert "只使用 2026-04-07 当天的信息" in prompt
    assert "不要出现以下对象或相关内容：华为" in prompt
    assert "只输出正文" in prompt
    assert "严禁出现“没有新官宣但… / 虽然没有官宣… / 值得关注的是行业信号…”" in prompt
    assert "禁止为了凑图生成无关泛图" in prompt
    assert "普通读者" not in prompt
    assert "普通人" not in prompt


@pytest.mark.asyncio
async def test_write_stage_blocks_forced_news_filler_phrases(monkeypatch, tmp_path: Path):
    module = _load_module(
        "article_publisher_write_reject_public_facing_test",
        "extension/skills/learned/article_publisher/scripts/ap_stages/write.py",
    )

    async def fake_generate_article_json(*_args, **_kwargs):
        return {
            "title": "今日中国 AI 新闻速览",
            "author": "测试作者",
            "digest": "测试摘要",
            "cover_prompt": "editorial illustration for china ai news",
            "sections": [
                {
                    "content": "<h2>观察</h2><p>虽然没有官宣，但我们仍可看到行业信号。</p><p>"
                    + ("正文内容" * 120)
                    + "</p><p style=\"margin-bottom:1.5em;\"></p>",
                    "image_prompt": None,
                }
            ],
        }

    monkeypatch.setattr(module, "_generate_article_json", fake_generate_article_json)

    result = await module.write_stage(
        topic=RAW_TOPIC,
        research_data={
            "topic": RAW_TOPIC,
            "subject": "中国最新人工智能周边新闻",
            "current_date": "2026-04-07",
            "source_type": "web",
            "context": "Src: https://example.com/a\n2026-04-07 中国 AI 新闻摘要。",
            "news_validation": {
                "recommend_reject": False,
                "same_day_only": True,
            },
        },
        output_dir=str(tmp_path),
        word_count=900,
        current_date="2026-04-07",
    )

    assert result.ok is False
    assert "新闻硬凑表述" in str(result.error or "")


@pytest.mark.asyncio
async def test_execute_hard_stops_before_write_when_news_insufficient(monkeypatch):
    module = _load_module(
        "article_publisher_execute_reject_public_facing_test",
        "extension/skills/learned/article_publisher/scripts/execute.py",
    )

    async def fake_search_stage(*_args, **_kwargs):
        return SimpleNamespace(
            ok=True,
            data={
                "topic": RAW_TOPIC,
                "subject": "中国最新人工智能周边新闻",
                "current_date": "2026-04-07",
                "source_type": "web",
                "context": "只有一条泛化素材",
                "news_validation": {
                    "recommend_reject": True,
                    "same_day_only": True,
                    "reject_reasons": ["候选新闻来源不足"],
                    "reject_message": "今日未发现足够支撑发布的【中国最新人工智能周边新闻】当天新闻，不建议发文。",
                },
            },
            failure_mode=None,
            error=None,
            files={},
            output_path=None,
        )

    async def fake_write_stage(*_args, **_kwargs):
        raise AssertionError("write_stage should not be called")

    async def fake_illustrate_stage(*_args, **_kwargs):
        raise AssertionError("illustrate_stage should not be called")

    async def fake_publish_stage(*_args, **_kwargs):
        raise AssertionError("publish_stage should not be called")

    monkeypatch.setattr(module, "search_stage", fake_search_stage)
    monkeypatch.setattr(module, "write_stage", fake_write_stage)
    monkeypatch.setattr(module, "illustrate_stage", fake_illustrate_stage)
    monkeypatch.setattr(module, "publish_stage", fake_publish_stage)
    monkeypatch.setattr(
        module,
        "get_credential",
        lambda *_args, **_kwargs: _async_value(None),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "get_credential_entry",
        lambda *_args, **_kwargs: _async_value(None),
    )

    class _ExecCtx:
        def __init__(self):
            self.message = SimpleNamespace(text="", user=SimpleNamespace(id="user-1"))

        async def run_skill(self, skill_name: str, _params: dict):
            if skill_name == "daily_query":
                return {"text": "2026-04-07 10:00:00"}
            raise AssertionError(f"unexpected skill call: {skill_name}")

    chunks = await _collect_chunks(
        module.execute(
            _ExecCtx(),
            {"topic": RAW_TOPIC, "publish": True},
            runtime=None,
        )
    )

    final = chunks[-1]
    assert final.get("ok") is False
    assert "今日未发现足够支撑发布的【中国最新人工智能周边新闻】当天新闻，不建议发文。" in str(
        final.get("text") or ""
    )


def test_normalize_article_data_strips_non_body_html():
    module = _load_module(
        "article_publisher_utils_public_facing_test",
        "extension/skills/learned/article_publisher/scripts/ap_utils/__init__.py",
    )

    normalized = module.normalize_article_data(
        {
            "title": "测试标题",
            "author": "测试作者",
            "digest": "测试摘要",
            "cover_prompt": None,
            "sections": [
                {
                    "content": (
                        "<p>以下为正文</p><h2>今天有哪些新消息</h2><p>这里是正文。</p><p>END</p>"
                    ),
                    "image_prompt": None,
                }
            ],
        },
        "测试主题",
    )

    content = normalized["sections"][0]["content"]
    assert "以下为正文" not in content
    assert "END" not in content
    assert "这里是正文" in content


def test_derive_topic_requirements_does_not_force_news_mode_for_generic_article():
    module = _load_module(
        "article_publisher_utils_generic_article_test",
        "extension/skills/learned/article_publisher/scripts/ap_utils/__init__.py",
    )

    requirements = module.derive_topic_requirements(
        "用article_publisher技能写一篇关于最新 AI 工作流教程的公众号文章",
        current_date="2026-04-07",
    )

    assert requirements["subject"] == "最新 AI 工作流教程"
    assert requirements["explicit_news_request"] is False
    assert requirements["same_day_only"] is False
    assert requirements["prefer_news"] is False
    assert requirements["search_query"] == "最新 AI 工作流教程"


async def _async_value(value):
    return value
