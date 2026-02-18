from types import SimpleNamespace

import pytest

from handlers import ai_handlers


@pytest.mark.asyncio
async def test_fetch_user_memory_snapshot_reads_markdown_store(monkeypatch):
    def _fake_load_snapshot(_uid: str, **kwargs):
        del kwargs
        return "【长期记忆（MEMORY.md）】\n- 居住地：江苏无锡"

    monkeypatch.setattr(
        ai_handlers.markdown_memory_store,
        "load_snapshot",
        _fake_load_snapshot,
    )
    rendered = await ai_handlers._fetch_user_memory_snapshot("u-1")
    assert "居住地：江苏无锡" in rendered


@pytest.mark.asyncio
async def test_should_include_memory_summary_for_task_short_request():
    assert (
        await ai_handlers._should_include_memory_summary_for_task("我住哪", "") is True
    )


@pytest.mark.asyncio
async def test_build_worker_instruction_with_context_uses_manager_memory(monkeypatch):
    async def _fake_collect(
        _ctx, *, user_id, current_user_message, max_messages=6, max_chars=1200
    ):
        del user_id, current_user_message, max_messages, max_chars
        return "- 用户: 上次让你记住我住在江苏无锡"

    async def _fake_fetch(_uid: str):
        return "- 居住地：江苏无锡"

    async def _fake_need_memory(_msg: str, _ctx: str) -> bool:
        return True

    monkeypatch.setattr(ai_handlers, "_collect_recent_dialog_context", _fake_collect)
    monkeypatch.setattr(ai_handlers, "_fetch_user_memory_snapshot", _fake_fetch)
    monkeypatch.setattr(
        ai_handlers, "_should_include_memory_summary_for_task", _fake_need_memory
    )

    instruction, meta = await ai_handlers._build_worker_instruction_with_context(
        SimpleNamespace(),
        user_id="u-1",
        user_message="我住哪",
        worker_has_memory=False,
    )
    assert "【用户记忆摘要（由 Manager 提供）】" in instruction
    assert meta["memory_summary_requested"] is True
    assert meta["memory_summary_included"] is True


@pytest.mark.asyncio
async def test_build_worker_instruction_skips_memory_in_group_session(monkeypatch):
    async def _fake_collect(
        _ctx, *, user_id, current_user_message, max_messages=6, max_chars=1200
    ):
        del user_id, current_user_message, max_messages, max_chars
        return "- 用户: 我住在江苏无锡"

    async def _fake_fetch(_uid: str):
        raise AssertionError("group session should not load personal memory")

    async def _fake_need_memory(_msg: str, _ctx: str) -> bool:
        return True

    ctx = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(type="group")))
    monkeypatch.setattr(ai_handlers, "_collect_recent_dialog_context", _fake_collect)
    monkeypatch.setattr(ai_handlers, "_fetch_user_memory_snapshot", _fake_fetch)
    monkeypatch.setattr(
        ai_handlers, "_should_include_memory_summary_for_task", _fake_need_memory
    )

    instruction, meta = await ai_handlers._build_worker_instruction_with_context(
        ctx,
        user_id="u-1",
        user_message="我住哪",
        worker_has_memory=False,
    )
    assert "【用户记忆摘要（由 Manager 提供）】" not in instruction
    assert meta["private_session"] is False
    assert meta["memory_summary_requested"] is False


@pytest.mark.asyncio
async def test_build_worker_instruction_with_context_skips_memory_when_worker_has_it(
    monkeypatch,
):
    async def _fake_collect(
        _ctx, *, user_id, current_user_message, max_messages=6, max_chars=1200
    ):
        del user_id, current_user_message, max_messages, max_chars
        return ""

    async def _fake_fetch(_uid: str):
        raise AssertionError("should not fetch memory when worker_has_memory=True")

    async def _fake_need_memory(_msg: str, _ctx: str) -> bool:
        return True

    monkeypatch.setattr(ai_handlers, "_collect_recent_dialog_context", _fake_collect)
    monkeypatch.setattr(ai_handlers, "_fetch_user_memory_snapshot", _fake_fetch)
    monkeypatch.setattr(
        ai_handlers, "_should_include_memory_summary_for_task", _fake_need_memory
    )

    instruction, meta = await ai_handlers._build_worker_instruction_with_context(
        SimpleNamespace(),
        user_id="u-2",
        user_message="按我偏好推荐新闻",
        worker_has_memory=True,
    )
    assert "【用户记忆摘要（由 Manager 提供）】" not in instruction
    assert meta["memory_summary_requested"] is True
    assert meta["memory_summary_included"] is False


@pytest.mark.asyncio
async def test_build_worker_instruction_with_context_skips_memory_when_not_needed(
    monkeypatch,
):
    async def _fake_collect(
        _ctx, *, user_id, current_user_message, max_messages=6, max_chars=1200
    ):
        del user_id, current_user_message, max_messages, max_chars
        return "- 用户: 请部署仓库"

    async def _fake_need_memory(_msg: str, _ctx: str) -> bool:
        return False

    async def _fake_fetch(_uid: str):
        raise AssertionError("should not fetch memory when not needed")

    monkeypatch.setattr(ai_handlers, "_collect_recent_dialog_context", _fake_collect)
    monkeypatch.setattr(ai_handlers, "_fetch_user_memory_snapshot", _fake_fetch)
    monkeypatch.setattr(
        ai_handlers, "_should_include_memory_summary_for_task", _fake_need_memory
    )

    instruction, meta = await ai_handlers._build_worker_instruction_with_context(
        SimpleNamespace(),
        user_id="u-3",
        user_message="部署这个仓库",
        worker_has_memory=False,
    )
    assert "【近期对话上下文】" in instruction
    assert "【用户记忆摘要（由 Manager 提供）】" not in instruction
    assert meta["memory_summary_requested"] is False
    assert meta["memory_summary_included"] is False


def test_pop_pending_ui_payload_merges_blocks():
    user_data = {
        "pending_ui": [
            {
                "actions": [
                    [{"text": "刷新", "callback_data": "rss_refresh"}],
                ]
            },
            {
                "actions": [
                    [{"text": "取消", "callback_data": "rss_cancel"}],
                ]
            },
        ]
    }

    ui_payload = ai_handlers._pop_pending_ui_payload(user_data)
    assert "pending_ui" not in user_data
    assert ui_payload is not None
    assert len(ui_payload["actions"]) == 2
    assert ui_payload["actions"][0][0]["text"] == "刷新"
    assert ui_payload["actions"][1][0]["text"] == "取消"


def test_stream_cut_index_prefers_sentence_boundary():
    text = "第一段内容。\n第二段内容继续输出"
    cut = ai_handlers._stream_cut_index(text, 14)
    assert cut <= 14
    assert cut >= 6
    assert text[:cut].endswith(("。", "\n"))


def test_stream_cut_index_falls_back_to_limit_without_boundary():
    text = "abcdefghijklmnopqrstuvwxyz"
    cut = ai_handlers._stream_cut_index(text, 10)
    assert cut == 10
