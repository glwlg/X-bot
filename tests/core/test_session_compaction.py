from types import SimpleNamespace

import pytest

from core.state_store import (
    get_session_entries,
    save_message,
    search_messages,
)
from handlers.service_handlers import compact_command
from services.session_compaction_service import (
    SESSION_MEMORY_PREFIX,
    SESSION_SUMMARY_PREFIX,
    session_compaction_service,
)
from user_context import SESSION_ID_KEY, get_user_context


class _DummyContext:
    def __init__(self, session_id: str):
        self.message = SimpleNamespace(
            user=SimpleNamespace(id="u-1"),
            chat=SimpleNamespace(id="chat-1", type="private"),
            platform="telegram",
            text="/compact",
        )
        self.platform_ctx = SimpleNamespace(user_data={SESSION_ID_KEY: session_id})
        self.replies: list[str] = []

    @property
    def user_data(self):
        return self.platform_ctx.user_data

    async def reply(self, text, **kwargs):
        _ = kwargs
        self.replies.append(str(text))
        return None


@pytest.mark.asyncio
async def test_session_compaction_rolls_older_dialog_into_single_summary(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    session_id = "sess-compact"
    await save_message(
        "u-1",
        "system",
        f"{SESSION_MEMORY_PREFIX}\n- 用户常住无锡",
        session_id,
    )
    for index in range(105):
        role = "user" if index % 2 == 0 else "model"
        await save_message("u-1", role, f"第 {index} 条消息", session_id)

    async def _fake_summary(**kwargs):
        _ = kwargs
        return "- 偏好：关注天气\n- 历史：更早对话已压缩"

    monkeypatch.setattr(
        session_compaction_service,
        "_summarize_history",
        _fake_summary,
    )

    result = await session_compaction_service.compact_session(
        user_id="u-1",
        session_id=session_id,
        force=False,
    )

    rows = await get_session_entries("u-1", session_id)
    dialog_rows = [row for row in rows if row["role"] in {"user", "model"}]

    assert result["ok"] is True
    assert result["compacted"] is True
    assert result["compressed_count"] == 95
    assert len(dialog_rows) == 10
    assert rows[0]["content"].startswith(SESSION_MEMORY_PREFIX)
    assert rows[1]["content"].startswith(SESSION_SUMMARY_PREFIX)
    assert rows[2]["content"] == "第 95 条消息"


@pytest.mark.asyncio
async def test_get_user_context_keeps_hidden_system_rows_but_search_skips_them(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    session_id = "sess-hidden"
    await save_message(
        "u-1",
        "system",
        f"{SESSION_SUMMARY_PREFIX}\n- 隐藏关键词：秘密项目",
        session_id,
    )
    await save_message("u-1", "user", "请继续秘密项目", session_id)
    await save_message("u-1", "model", "好的，继续推进。", session_id)

    async def _fake_load_snapshot(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(
        "user_context.long_term_memory.load_user_snapshot",
        _fake_load_snapshot,
    )

    ctx = _DummyContext(session_id)
    history = await get_user_context(
        ctx,
        "u-1",
        include_hidden_system=True,
        auto_compact=False,
    )
    matched = await search_messages("u-1", "秘密项目", session_id=session_id, limit=10)

    assert history[0]["role"] == "system"
    assert history[0]["parts"][0]["text"].startswith(SESSION_SUMMARY_PREFIX)
    assert len(matched) == 1
    assert matched[0]["role"] == "user"
    assert matched[0]["content"] == "请继续秘密项目"


@pytest.mark.asyncio
async def test_compact_command_reports_compacted_count(monkeypatch):
    async def _allow(_ctx):
        return True

    async def _fake_compact(_ctx, _user_id, *, force):
        assert force is True
        return {
            "ok": True,
            "compacted": True,
            "compressed_count": 90,
            "kept_recent": 10,
        }

    monkeypatch.setattr("handlers.service_handlers.check_permission_unified", _allow)
    monkeypatch.setattr("handlers.service_handlers.compact_current_session", _fake_compact)

    ctx = _DummyContext("sess-command")
    await compact_command(ctx)

    assert ctx.replies == ["🗜️ 已压缩 90 条历史，保留最近 10 条原始消息。"]
