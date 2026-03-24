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
                task_tracking=True,
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
                task_tracking=False,
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
                '{"request_mode":"task","task_tracking":true,"candidate_skills":["web_search","unknown","download_video",'
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
        assert decision.task_tracking is True

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

        assert decision.request_mode == "chat"
        assert decision.candidate_skills == []
        assert decision.task_tracking is False

    @pytest.mark.asyncio
    async def test_intent_router_uses_model_for_chat_turn(self, monkeypatch):
        from services.intent_router import intent_router

        calls = []

        async def _fake_generate_text(**kwargs):
            calls.append(kwargs)
            return (
                '{"request_mode":"chat","task_tracking":false,'
                '"candidate_skills":[],"reason":"greeting","confidence":0.93}'
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
            dialog_messages=[{"role": "user", "content": "你好呀"}],
            candidates=[],
        )

        assert len(calls) == 1
        assert decision.request_mode == "chat"
        assert decision.task_tracking is False
        assert decision.reason == "greeting"

    @pytest.mark.asyncio
    async def test_intent_router_fails_over_to_backup_routing_model(
        self, monkeypatch
    ):
        from services.intent_router import intent_router

        attempts = []
        failed_models = []
        succeeded_models = []

        async def _fake_generate_text(**kwargs):
            attempts.append(kwargs.get("model"))
            if kwargs.get("model") == "routing/primary":
                raise RuntimeError("auth_unavailable: no auth available")
            return (
                '{"request_mode":"task","task_tracking":true,"candidate_skills":[],"reason":"backup",'
                '"confidence":0.75}'
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
            lambda: "routing/primary",
        )
        monkeypatch.setattr(
            "services.intent_router.get_model_candidates_for_input",
            lambda *args, **kwargs: ["routing/primary", "routing/backup"],
        )
        monkeypatch.setattr(
            "services.intent_router.mark_model_failed",
            lambda model_key: failed_models.append(model_key),
        )
        monkeypatch.setattr(
            "services.intent_router.mark_model_success",
            lambda model_key: succeeded_models.append(model_key),
        )

        decision = await intent_router.route(
            dialog_messages=[{"role": "user", "content": "帮我查一下日志"}],
            candidates=[],
        )

        assert attempts == ["routing/primary", "routing/backup"]
        assert failed_models == ["routing/primary"]
        assert succeeded_models == ["routing/backup"]
        assert decision.request_mode == "task"
        assert decision.reason == "backup"
        assert decision.confidence == 0.75
        assert decision.task_tracking is True


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


class TestImageInputService:
    @pytest.mark.asyncio
    async def test_fetch_image_from_url_accepts_image_payload(self, monkeypatch):
        import httpx
        from services import image_input_service

        class _FakeResponse:
            def __init__(self, *, headers, chunks, status_code=200):
                self.headers = headers
                self._chunks = list(chunks)
                self.status_code = status_code
                self.request = httpx.Request("GET", "https://example.com/cam.jpg")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                _ = (exc_type, exc, tb)
                return False

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        "boom",
                        request=self.request,
                        response=self,
                    )

            async def aiter_bytes(self):
                for chunk in self._chunks:
                    yield chunk

        class _FakeClient:
            def __init__(self, response):
                self._response = response

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                _ = (exc_type, exc, tb)
                return False

            def stream(self, method, url, headers=None):
                _ = (method, url, headers)
                return self._response

        response = _FakeResponse(
            headers={"Content-Type": "image/png"},
            chunks=[b"\x89PNG\r\n\x1a\npayload"],
        )
        monkeypatch.setattr(
            image_input_service.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeClient(response),
        )

        payload, mime_type = await image_input_service.fetch_image_from_url(
            "https://example.com/cam.jpg"
        )

        assert payload.startswith(b"\x89PNG")
        assert mime_type == "image/png"

    @pytest.mark.asyncio
    async def test_fetch_image_from_url_rejects_non_image_payload(self, monkeypatch):
        from services import image_input_service

        class _FakeResponse:
            def __init__(self):
                self.headers = {"Content-Type": "text/html"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                _ = (exc_type, exc, tb)
                return False

            def raise_for_status(self):
                return None

            async def aiter_bytes(self):
                yield b"<html>hello</html>"

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                _ = (exc_type, exc, tb)
                return False

            def stream(self, method, url, headers=None):
                _ = (method, url, headers)
                return _FakeResponse()

        monkeypatch.setattr(
            image_input_service.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeClient(),
        )

        with pytest.raises(image_input_service.ImageInputDownloadError):
            await image_input_service.fetch_image_from_url(
                "https://example.com/not-image"
            )
