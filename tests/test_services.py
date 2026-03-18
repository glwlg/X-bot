"""
Services 模块单元测试
"""

import pytest


class TestIntentRouter:
    """测试统一请求路由"""

    @pytest.mark.asyncio
    async def test_intent_router_classify_returns_task_or_chat(self, monkeypatch):
        from services.intent_router import RoutingDecision, intent_router

        async def _fake_route_task(**_kwargs):
            return RoutingDecision(
                request_mode="task",
                candidate_skills=[],
                confidence=0.9,
                reason="task",
            )

        monkeypatch.setattr(intent_router, "route", _fake_route_task)
        task_decision = await intent_router.classify("请部署 n8n")

        assert task_decision.request_mode == "task"
        assert task_decision.confidence == 0.9
        assert task_decision.reason == "task"

        async def _fake_route_chat(**_kwargs):
            return RoutingDecision(
                request_mode="chat",
                candidate_skills=[],
                confidence=0.7,
                reason="chat",
            )

        monkeypatch.setattr(intent_router, "route", _fake_route_chat)
        chat_decision = await intent_router.classify("你好")

        assert chat_decision.request_mode == "chat"
        assert chat_decision.confidence == 0.7
        assert chat_decision.reason == "chat"

    @pytest.mark.asyncio
    async def test_intent_router_filters_to_known_top5(self, monkeypatch):
        from core.extension_router import ExtensionCandidate
        from services.intent_router import intent_router

        async def _fake_generate_text(**kwargs):
            _ = kwargs
            return (
                '{"request_mode":"task","candidate_skills":["web_search","unknown","download_video",'
                '"rss_subscribe","web_search","news_digest","skill_manager"],'
                '"reason":"matched","confidence":0.82}'
            )

        monkeypatch.setattr(
            "services.intent_router.generate_text",
            _fake_generate_text,
        )
        monkeypatch.setattr(
            "services.intent_router.get_client_for_model",
            lambda *_args, **_kwargs: object(),
        )
        monkeypatch.setattr(
            "services.intent_router.get_routing_model",
            lambda: "routing/test",
        )

        decision = await intent_router.route(
            dialog_messages=[
                {"role": "user", "content": "帮我查新闻并顺手看看这个视频"}
            ],
            candidates=[
                ExtensionCandidate(
                    name="web_search",
                    description="网页搜索",
                    tool_name="ext_web_search",
                ),
                ExtensionCandidate(
                    name="download_video",
                    description="下载视频",
                    tool_name="ext_download_video",
                ),
                ExtensionCandidate(
                    name="rss_subscribe",
                    description="RSS 订阅",
                    tool_name="ext_rss_subscribe",
                ),
                ExtensionCandidate(
                    name="news_digest",
                    description="新闻摘要",
                    tool_name="ext_news_digest",
                ),
                ExtensionCandidate(
                    name="skill_manager",
                    description="技能管理",
                    tool_name="ext_skill_manager",
                ),
                ExtensionCandidate(
                    name="extra_skill",
                    description="额外技能",
                    tool_name="ext_extra_skill",
                ),
            ],
        )

        assert decision.request_mode == "task"
        assert decision.candidate_skills == [
            "web_search",
            "download_video",
            "rss_subscribe",
            "news_digest",
            "skill_manager",
        ]
        assert decision.confidence == 0.82

    @pytest.mark.asyncio
    async def test_intent_router_returns_task_fallback_on_invalid_json(
        self, monkeypatch
    ):
        from core.extension_router import ExtensionCandidate
        from services.intent_router import intent_router

        async def _fake_generate_text(**kwargs):
            _ = kwargs
            return "not-json"

        monkeypatch.setattr(
            "services.intent_router.generate_text",
            _fake_generate_text,
        )
        monkeypatch.setattr(
            "services.intent_router.get_client_for_model",
            lambda *_args, **_kwargs: object(),
        )
        monkeypatch.setattr(
            "services.intent_router.get_routing_model",
            lambda: "routing/test",
        )

        decision = await intent_router.route(
            dialog_messages=[{"role": "user", "content": "你好"}],
            candidates=[
                ExtensionCandidate(
                    name="web_search",
                    description="网页搜索",
                    tool_name="ext_web_search",
                )
            ],
        )

        assert decision.request_mode == "task"
        assert decision.candidate_skills == []


class TestWebSummaryService:
    """测试网页摘要服务"""

    def test_extract_urls(self):
        """测试 URL 提取"""
        from services.web_summary_service import extract_urls

        text = "请看这个链接 https://example.com 和 http://test.org/page"
        urls = extract_urls(text)

        assert len(urls) == 2
        assert "https://example.com" in urls
        assert "http://test.org/page" in urls

    def test_extract_urls_no_match(self):
        """测试无 URL 的文本"""
        from services.web_summary_service import extract_urls

        text = "这是一段没有链接的文本"
        urls = extract_urls(text)

        assert len(urls) == 0

    @pytest.mark.asyncio
    async def test_fetch_webpage_content_prefers_playwright_cli(self, monkeypatch):
        from services import web_summary_service as web_service

        async def _fake_cli(_url: str):
            return "【通过 Playwright CLI 获取的页面 Markdown 快照】\n\n# From CLI"

        monkeypatch.setenv("WEB_BROWSER_PREFER_PLAYWRIGHT_CLI", "true")
        monkeypatch.setattr(
            web_service,
            "fetch_with_playwright_cli_snapshot",
            _fake_cli,
        )

        content = await web_service.fetch_webpage_content("https://example.com")

        assert content is not None
        assert "Playwright CLI" in content
        assert "From CLI" in content

    @pytest.mark.asyncio
    async def test_fetch_webpage_content_http_fallback(self, monkeypatch):
        from services import web_summary_service as web_service

        async def _fake_cli(_url: str):
            return None

        class _FakeResponse:
            text = "<html><body><h1>fallback</h1></body></html>"

            def raise_for_status(self):
                return None

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                _ = (exc_type, exc, tb)
                return False

            async def get(self, _url: str, headers=None):
                _ = headers
                return _FakeResponse()

        monkeypatch.setenv("WEB_BROWSER_PREFER_PLAYWRIGHT_CLI", "false")
        monkeypatch.setattr(
            web_service,
            "fetch_with_playwright_cli_snapshot",
            _fake_cli,
        )
        monkeypatch.setattr(
            web_service.httpx, "AsyncClient", lambda **kwargs: _FakeClient()
        )

        content = await web_service.fetch_webpage_content("https://example.com")

        assert content is not None
        assert "HTTP 原始页面内容" in content
        assert "fallback" in content
