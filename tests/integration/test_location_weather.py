import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from handlers.ai_handlers import _build_subagent_instruction_with_context


@pytest.fixture
def mock_ctx():
    return AsyncMock()


@pytest.fixture
def mock_fetch_memory():
    with patch("handlers.ai_handlers._fetch_user_memory_snapshot") as mock:
        yield mock


class TestLocationWeatherIntegration:
    @pytest.mark.asyncio
    async def test_weather_dispatch_with_memory(
        self, mock_ctx, mock_fetch_memory
    ):
        # Scenario: User asks "How is the weather?"
        # Ikaros decides to include memory summary because it's location dependent.

        mock_fetch_memory.return_value = "- User lives in Beijing"

        instruction, meta = await _build_subagent_instruction_with_context(
            ctx=mock_ctx,
            user_id="user_123",
            user_message="How is the weather?",
            subagent_has_memory=False,
        )

        assert "User lives in Beijing" in instruction
        assert meta["memory_summary_included"] is True
        assert meta["memory_summary_requested"] is True

    @pytest.mark.asyncio
    async def test_empty_request_skips_memory(self, mock_ctx, mock_fetch_memory):
        # Scenario: User asks "echo hello"

        instruction, meta = await _build_subagent_instruction_with_context(
            ctx=mock_ctx,
            user_id="user_123",
            user_message="",
            subagent_has_memory=False,
        )

        assert "User lives in Beijing" not in instruction
        assert meta["memory_summary_included"] is False
        assert meta["memory_summary_requested"] is False
