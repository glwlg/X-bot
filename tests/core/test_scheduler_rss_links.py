import pytest

import core.scheduler as scheduler_module
from core.scheduler import _resolve_entry_link


def test_resolve_entry_link_prefers_source_url_over_google_redirect():
    entry = {
        "link": "https://news.google.com/rss/articles/CBMiabc123",
        "summary": (
            '<a href="https://news.example.com/story/42?from=rss">新闻标题</a>'
            '<font color="#6f6f6f">via Example</font>'
        ),
    }

    resolved = _resolve_entry_link(entry, fallback_url="https://fallback.example.com")
    assert resolved == "https://news.example.com/story/42?from=rss"


def test_resolve_entry_link_falls_back_when_no_source_url():
    entry = {
        "link": "https://news.google.com/rss/articles/CBMiabc123",
        "summary": "no links here",
    }

    resolved = _resolve_entry_link(entry, fallback_url="https://fallback.example.com")
    assert resolved == "https://news.google.com/rss/articles/CBMiabc123"


@pytest.mark.asyncio
async def test_trigger_manual_rss_check_busy_message_can_be_suppressed():
    await scheduler_module._rss_check_lock.acquire()
    try:
        visible = await scheduler_module.trigger_manual_rss_check(1)
        hidden = await scheduler_module.trigger_manual_rss_check(
            1,
            suppress_busy_message=True,
        )
    finally:
        scheduler_module._rss_check_lock.release()

    assert visible == "⚠️ 正在进行定时更新检查，请稍后再试。"
    assert hidden == ""
