import sys
import os
import asyncio
from unittest.mock import MagicMock, patch

# Adjust path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "src"))
sys.path.append(os.path.join(os.getcwd(), "skills", "builtin"))

# Mock repositories and services to avoid DB dependency
sys.modules["repositories"] = MagicMock()
sys.modules["services.stock_service"] = MagicMock()
sys.modules["core.platform.models"] = MagicMock()
sys.modules["core.scheduler"] = MagicMock()
sys.modules["core.config"] = MagicMock()

# Setup mocks
from repositories import (
    get_user_watchlist,
    add_watchlist_stock,
    remove_watchlist_stock_by_code,
)


# Make them async
async def mock_get_watchlist(*args, **kwargs):
    return [
        {"stock_code": "00700", "stock_name": "Tencent", "market": "HK"},
        {"stock_code": "AAPL", "stock_name": "Apple", "market": "US"},
    ]


get_user_watchlist.side_effect = mock_get_watchlist


async def mock_add_stock(*args, **kwargs):
    return True


add_watchlist_stock.side_effect = mock_add_stock


async def mock_remove_stock(*args, **kwargs):
    return True


remove_watchlist_stock_by_code.side_effect = mock_remove_stock

from services.stock_service import (
    fetch_stock_quotes,
    format_stock_message,
    search_stock_by_name,
)


async def mock_fetch(*args, **kwargs):
    return {"00700": "Tencent +1%", "AAPL": "Apple -1%"}


fetch_stock_quotes.side_effect = mock_fetch

format_stock_message.return_value = "📈 Markets UP\nTencent: +1%\nApple: -1%"


async def mock_search(*args, **kwargs):
    if "Tencent" in args[0]:
        return [{"code": "00700", "name": "Tencent", "market": "HK"}]
    return []


search_stock_by_name.side_effect = mock_search

# Import module under test
import importlib.util

spec = importlib.util.spec_from_file_location(
    "stock_execute", "skills/builtin/stock_watch/scripts/execute.py"
)
stock_execute = importlib.util.module_from_spec(spec)

# Mock UnifiedContext in the module
stock_execute.UnifiedContext = MagicMock()
stock_execute.InlineKeyboardButton = (
    MagicMock()
)  # We iterate over these in the code verify they behave like objects


class MockBtn:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data


stock_execute.InlineKeyboardButton = MockBtn
stock_execute.InlineKeyboardMarkup = MagicMock()

sys.modules["stock_execute"] = stock_execute
spec.loader.exec_module(stock_execute)


async def test_execution():
    ctx = MagicMock()
    ctx.message.user.id = 12345
    ctx.message.platform = "telegram"

    params = {"action": "list"}

    print("Running execute(action='list')...")
    result = await stock_execute.execute(ctx, params)

    print(f"Result Type: {type(result)}")
    print(f"Result Content: {result}")

    if isinstance(result, dict) and "text" in result and "ui" in result:
        print("✅ Validation Passed: Result is structured dict.")
        if "actions" in result["ui"]:
            print(f"✅ UI Actions found: {len(result['ui']['actions'])} rows")
    else:
        print("❌ Validation Failed: Result is not structured.")

    # Test Refresh
    print("\nRunning execute(action='refresh')...")
    # Mock trigger
    stock_execute.trigger_manual_stock_check = MagicMock()
    stock_execute.trigger_manual_stock_check.side_effect = None  # Async mock?

    async def mock_trigger(*args):
        return "New Quote Data"

    stock_execute.trigger_manual_stock_check = mock_trigger

    result_refresh = await stock_execute.execute(ctx, {"action": "refresh"})
    print(f"Refresh Result: {result_refresh}")

    if isinstance(result_refresh, dict) and "text" in result_refresh:
        print("✅ Refresh Passed.")
    else:
        print("❌ Refresh Failed.")


if __name__ == "__main__":
    asyncio.run(test_execution())
