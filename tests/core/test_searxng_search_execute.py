import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "builtin"
        / "searxng_search"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location("searxng_search_execute_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, results):
        self.status_code = 200
        self._results = list(results)

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": list(self._results)}


class _FakeAsyncClient:
    def __init__(self, responses, called_urls):
        self._responses = list(responses)
        self._called_urls = called_urls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False

    async def get(self, url):
        self._called_urls.append(str(url))
        if self._responses:
            return _FakeResponse(self._responses.pop(0))
        return _FakeResponse([])


@pytest.mark.asyncio
async def test_searxng_weather_query_reranks_authoritative_sources(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:28080")

    responses = [
        [
            {
                "title": "2026年_百度百科",
                "url": "https://baike.baidu.com/item/2026%E5%B9%B4/9536516",
                "content": "2026年是平年。",
            },
            {
                "title": "无锡天气预报",
                "url": "https://www.weather.com.cn/weather/101190201.shtml",
                "content": "无锡天气、体感温度、紫外线指数。",
            },
            {
                "title": "无锡空气质量",
                "url": "https://www.iqair.cn/cn/china/jiangsu/wuxi",
                "content": "AQI 和 PM2.5。",
            },
        ]
    ]
    called_urls: list[str] = []
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout=30.0: _FakeAsyncClient(responses, called_urls),
    )

    result = await module.execute(
        SimpleNamespace(),
        {"query": "江苏无锡 今日 天气 体感 AQI"},
        runtime=None,
    )

    text = str(result.get("text") or "")
    assert "weather.com.cn" in text
    assert "baike.baidu.com" in text
    assert text.find("weather.com.cn") < text.find("baike.baidu.com")
    assert called_urls
    assert "engines=google%2Cbing" in called_urls[0]


@pytest.mark.asyncio
async def test_searxng_search_applies_site_blocklist(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:28080")

    responses = [
        [
            {
                "title": "2026年_百度百科",
                "url": "https://baike.baidu.com/item/2026%E5%B9%B4/9536516",
                "content": "2026年是平年。",
            },
            {
                "title": "无锡天气预报",
                "url": "https://www.weather.com.cn/weather/101190201.shtml",
                "content": "无锡天气、体感温度、紫外线指数。",
            },
        ]
    ]
    called_urls: list[str] = []
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout=30.0: _FakeAsyncClient(responses, called_urls),
    )

    result = await module.execute(
        SimpleNamespace(),
        {
            "query": "无锡天气",
            "site_blocklist": ["baike.baidu.com"],
        },
        runtime=None,
    )

    text = str(result.get("text") or "")
    assert "weather.com.cn" in text
    assert "baike.baidu.com" not in text


@pytest.mark.asyncio
async def test_searxng_search_retries_general_category_once(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:28080")

    responses = [
        [],
        [
            {
                "title": "Python Tutorial",
                "url": "https://docs.python.org/3/tutorial/",
                "content": "Python docs tutorial.",
            }
        ],
    ]
    called_urls: list[str] = []
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout=30.0: _FakeAsyncClient(responses, called_urls),
    )

    result = await module.execute(
        SimpleNamespace(),
        {
            "query": "python tutorial",
            "categories": "news",
            "engines": ["google"],
            "language": "en-US",
        },
        runtime=None,
    )

    text = str(result.get("text") or "")
    assert "docs.python.org" in text
    assert len(called_urls) == 2
    assert "categories=news" in called_urls[0]
    assert "categories=general" in called_urls[1]


@pytest.mark.asyncio
async def test_searxng_search_news_profile_uses_profile_engines_and_category(
    monkeypatch,
):
    module = _load_module()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:28080")
    monkeypatch.setenv("SEARXNG_SEARCH_NEWS_CATEGORIES", "news")
    monkeypatch.setenv("SEARXNG_SEARCH_NEWS_ENGINES", "google,bing")

    responses = [
        [
            {
                "title": "今日要闻",
                "url": "https://news.cctv.com/2026/02/20/ARTIexample.shtml",
                "content": "新闻摘要",
            }
        ]
    ]
    called_urls: list[str] = []
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout=30.0: _FakeAsyncClient(responses, called_urls),
    )

    result = await module.execute(
        SimpleNamespace(),
        {
            "query": "今日头条",
            "intent_profile": "news",
        },
        runtime=None,
    )

    assert "cctv.com" in str(result.get("text") or "")
    assert called_urls
    assert "categories=news" in called_urls[0]
    assert "engines=google%2Cbing" in called_urls[0]


@pytest.mark.asyncio
async def test_searxng_search_tech_profile_strict_sources_filters_noise(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:28080")
    monkeypatch.setenv("SEARXNG_SEARCH_TECH_ALLOWLIST", "docs.python.org,github.com")
    monkeypatch.setenv("SEARXNG_SEARCH_TECH_STRICT_SOURCES", "true")

    responses = [
        [
            {
                "title": "2026年_百度百科",
                "url": "https://baike.baidu.com/item/2026%E5%B9%B4/9536516",
                "content": "2026年是平年。",
            },
            {
                "title": "Python Exceptions",
                "url": "https://docs.python.org/3/tutorial/errors.html",
                "content": "How to handle exceptions.",
            },
        ]
    ]
    called_urls: list[str] = []
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout=30.0: _FakeAsyncClient(responses, called_urls),
    )

    result = await module.execute(
        SimpleNamespace(),
        {
            "query": "python exception stack trace",
            "intent_profile": "tech",
        },
        runtime=None,
    )

    text = str(result.get("text") or "")
    assert "docs.python.org" in text
    assert "baike.baidu.com" not in text


@pytest.mark.asyncio
async def test_searxng_search_auto_detects_tech_profile(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:28080")
    monkeypatch.setenv("SEARXNG_SEARCH_TECH_CATEGORIES", "it")
    monkeypatch.setenv("SEARXNG_SEARCH_TECH_ENGINES", "google")

    responses = [
        [
            {
                "title": "Python Exception Handling",
                "url": "https://docs.python.org/3/tutorial/errors.html",
                "content": "How to handle exceptions.",
            }
        ]
    ]
    called_urls: list[str] = []
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout=30.0: _FakeAsyncClient(responses, called_urls),
    )

    result = await module.execute(
        SimpleNamespace(),
        {
            "query": "python exception stack trace",
        },
        runtime=None,
    )

    assert "docs.python.org" in str(result.get("text") or "")
    assert called_urls
    assert "categories=it" in called_urls[0]
    assert "engines=google" in called_urls[0]
