"""
Services 模块单元测试
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestStockService:
    """测试股票服务"""

    def test_format_stock_message_empty(self):
        """测试格式化空股票列表"""
        from services.stock_service import format_stock_message

        result = format_stock_message([])
        assert "暂无" in result or result == ""

    def test_format_stock_message_with_data(self, sample_stock_data):
        """测试格式化股票消息"""
        from services.stock_service import format_stock_message

        # 模拟完整的股票数据（使用实际字段名）
        quotes = [
            {
                "code": "sh601006",
                "name": "大秦铁路",
                "price": 7.50,
                "change": 0.15,
                "percent": 2.04,
            },
            {
                "code": "sz000001",
                "name": "平安银行",
                "price": 12.30,
                "change": -0.20,
                "percent": -1.60,
            },
        ]

        result = format_stock_message(quotes)

        assert "大秦铁路" in result
        assert "平安银行" in result


class TestIntentRouter:
    """测试意图路由"""

    @pytest.mark.asyncio
    async def test_intent_router_classify_maps_route_to_task_or_chat(self, monkeypatch):
        """测试路由结果映射为 task/chat"""
        from services.intent_router import DispatchDecision, intent_router

        async def _fake_route_task(_message: str):
            return DispatchDecision(route="worker_task", confidence=0.9, reason="task")

        monkeypatch.setattr(intent_router, "route", _fake_route_task)
        task_decision = await intent_router.classify("请部署 n8n")

        assert task_decision.intent == "task"
        assert task_decision.confidence == 0.9
        assert task_decision.reason == "task"

        async def _fake_route_chat(_message: str):
            return DispatchDecision(route="manager_chat", confidence=0.7, reason="chat")

        monkeypatch.setattr(intent_router, "route", _fake_route_chat)
        chat_decision = await intent_router.classify("你好")

        assert chat_decision.intent == "chat"
        assert chat_decision.confidence == 0.7
        assert chat_decision.reason == "chat"


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

    def test_is_video_platform(self):
        """测试视频平台检测"""
        from services.web_summary_service import is_video_platform

        assert is_video_platform("https://www.youtube.com/watch?v=abc") is True
        assert is_video_platform("https://twitter.com/user/status/123") is True
        assert is_video_platform("https://x.com/user/status/123") is True
        assert is_video_platform("https://example.com/page") is False


class TestDownloadService:
    """测试下载服务"""

    def test_download_result_dataclass(self):
        """测试下载结果数据类"""
        from services.download_service import DownloadResult

        result = DownloadResult(
            success=True,
            file_path="/tmp/video.mp4",
            is_too_large=False,
            file_size_mb=25.5,
        )

        assert result.success is True
        assert result.file_path == "/tmp/video.mp4"
        assert result.is_too_large is False
        assert result.file_size_mb == 25.5
