from core.extension_router import ExtensionRouter
import core.extension_router as extension_router_module


def test_extension_router_matches_relevant_skills(monkeypatch):
    monkeypatch.setattr(
        extension_router_module.skill_loader,
        "get_skills_summary",
        lambda: [
            {
                "name": "rss_subscribe",
                "description": "Subscribe to RSS feeds and manage RSS sources",
                "triggers": ["rss", "订阅", "feed"],
                "input_schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
            {
                "name": "docker_ops",
                "description": "Docker container operations",
                "triggers": ["docker", "容器"],
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
    )

    router = ExtensionRouter()
    candidates = router.route("帮我订阅这个 RSS 地址", max_candidates=3)

    assert candidates
    assert candidates[0].name == "rss_subscribe"
    assert candidates[0].tool_name == "ext_rss_subscribe"


def test_extension_router_returns_empty_when_no_match(monkeypatch):
    monkeypatch.setattr(
        extension_router_module.skill_loader,
        "get_skills_summary",
        lambda: [
            {
                "name": "rss_subscribe",
                "description": "Subscribe to RSS feeds",
                "triggers": ["rss"],
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    router = ExtensionRouter()
    candidates = router.route("请帮我做矩阵特征值分解", max_candidates=3)

    assert candidates == []


def test_extension_router_matches_research_keyword_fallback(monkeypatch):
    monkeypatch.setattr(
        extension_router_module.skill_loader,
        "get_skills_summary",
        lambda: [
            {
                "name": "deep_research",
                "description": "深度研究和分析报告生成",
                "triggers": ["深度研究", "deep research"],
                "input_schema": {
                    "type": "object",
                    "properties": {"topic": {"type": "string"}},
                    "required": ["topic"],
                },
            },
            {
                "name": "download_video",
                "description": "下载视频",
                "triggers": ["下载", "video"],
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
    )

    router = ExtensionRouter()
    candidates = router.route("做一份 minimax 2.5 的研究报告", max_candidates=3)

    assert candidates
    assert candidates[0].name == "deep_research"


def test_extension_router_news_intent_prefers_search_tools(monkeypatch):
    monkeypatch.setattr(
        extension_router_module.skill_loader,
        "get_skills_summary",
        lambda: [
            {
                "name": "searxng_search",
                "description": "聚合网络搜索",
                "triggers": ["搜索", "search"],
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "news_article_writer",
                "description": "搜索最新新闻并撰写公众号文章",
                "triggers": ["写公众号文章", "生成新闻文章"],
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
    )

    router = ExtensionRouter()
    candidates = router.route("今天有什么有意思的新闻", max_candidates=3)

    assert candidates
    assert candidates[0].name == "searxng_search"


def test_extension_router_news_writing_intent_keeps_writer_candidate(monkeypatch):
    monkeypatch.setattr(
        extension_router_module.skill_loader,
        "get_skills_summary",
        lambda: [
            {
                "name": "searxng_search",
                "description": "聚合网络搜索",
                "triggers": ["搜索", "search"],
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "news_article_writer",
                "description": "搜索最新新闻并撰写公众号文章",
                "triggers": ["写公众号文章", "生成新闻文章"],
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
    )

    router = ExtensionRouter()
    candidates = router.route("写一篇今天AI新闻文章并给出摘要", max_candidates=3)
    names = [item.name for item in candidates]

    assert "news_article_writer" in names
