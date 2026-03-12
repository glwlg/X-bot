from types import SimpleNamespace
from pathlib import Path

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


def test_summarize_manager_tool_args_for_bash_command():
    label, value = ai_handlers._summarize_manager_tool_args(
        "bash",
        {"command": "docker stop uptime-kuma"},
    )

    assert label == "命令"
    assert value == "docker stop uptime-kuma"


def test_build_manager_progress_text_includes_command_and_result():
    text = ai_handlers._build_manager_progress_text(
        {
            "event": "tool_call_finished",
            "task_id": "mgr-1",
            "turn": 3,
            "recent_steps": [
                {
                    "name": "bash",
                    "status": "done",
                    "summary": "Command executed with code 0",
                    "detail_label": "命令",
                    "detail": "docker stop uptime-kuma",
                }
            ],
        }
    )

    assert "任务ID：`mgr-1`" in text
    assert "动作：`Shell` 执行完成" in text
    assert "命令：`docker stop uptime-kuma`" in text
    assert "结果：Command executed with code 0" in text


def test_build_manager_progress_text_hides_verbose_tool_output():
    text = ai_handlers._build_manager_progress_text(
        {
            "event": "tool_call_finished",
            "task_id": "mgr-verbose",
            "turn": 2,
            "recent_steps": [
                {
                    "name": "load_skill",
                    "status": "done",
                    "summary": "{'ok': True, 'content': '# Daily Query\\n\\n这是日常基础查询 skill...'}",
                    "detail_label": "技能",
                    "detail": "daily_query",
                }
            ],
        }
    )

    assert "技能：`daily_query`" in text
    assert "结果：技能已加载，正在继续执行。" in text
    assert "Daily Query" not in text


@pytest.mark.asyncio
async def test_try_handle_waiting_confirmation_treats_text_as_adjustment(monkeypatch):
    class _FakeHeartbeatStore:
        async def get_session_active_task(self, user_id: str):
            _ = user_id
            return {"id": "mgr-1", "status": "waiting_user"}

        async def update_session_active_task(self, user_id: str, **kwargs):
            _ = (user_id, kwargs)
            return None

        async def release_lock(self, user_id: str):
            _ = user_id
            return True

        async def append_session_event(self, user_id: str, event: str):
            _ = (user_id, event)
            return None

    class _FakeClosureService:
        def __init__(self):
            self.calls: list[dict] = []

        async def resume_waiting_task(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {"ok": True, "message": "✅ 已记录你的补充说明，正在继续推进阶段 1/2。"}

    fake_service = _FakeClosureService()
    monkeypatch.setattr("core.heartbeat_store.heartbeat_store", _FakeHeartbeatStore())
    monkeypatch.setattr(
        "manager.relay.closure_service.manager_closure_service",
        fake_service,
    )

    replies: list[str] = []

    class _Ctx:
        message = SimpleNamespace(user=SimpleNamespace(id="u-1"))

        async def reply(self, text, **kwargs):
            _ = kwargs
            replies.append(str(text))
            return SimpleNamespace(id="reply")

    handled = await ai_handlers._try_handle_waiting_confirmation(
        _Ctx(),
        "把范围限制在最近 7 天，并先检查容器状态",
    )

    assert handled is True
    assert fake_service.calls == [
        {
            "user_id": "u-1",
            "user_message": "把范围限制在最近 7 天，并先检查容器状态",
            "source": "text",
        }
    ]
    assert "已记录你的补充说明" in replies[-1]


def test_build_runtime_phrase_pools_uses_static_indicator_frames():
    received, loading = ai_handlers._build_runtime_phrase_pools("u-1")

    assert received == ["..."]
    assert loading == [".", "..", "...", ".."]


class _DummyOutgoingMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id
        self.id = message_id

    async def delete(self):
        return True


class _DummyChatContext:
    def __init__(self):
        self.message = SimpleNamespace(
            text="帮我画一张猫狗大战",
            chat=SimpleNamespace(id="c-1", type="private"),
            user=SimpleNamespace(id="u-1"),
            platform="telegram",
            reply_to_message=None,
            raw_data={},
            id="msg-1",
        )
        self.platform_ctx = SimpleNamespace(user_data={})
        self.user_data = {}
        self._adapter = SimpleNamespace(can_update_message=True)
        self.replies: list[tuple[object, dict]] = []
        self.edits: list[tuple[object, object, dict]] = []
        self.photos: list[tuple[object, object, dict]] = []
        self.documents: list[tuple[object, object, object, dict]] = []
        self.actions: list[tuple[str, dict]] = []

    async def reply(self, payload, **kwargs):
        self.replies.append((payload, dict(kwargs)))
        return _DummyOutgoingMessage(len(self.replies))

    async def edit_message(self, message_id, text, **kwargs):
        self.edits.append((message_id, text, dict(kwargs)))
        return True

    async def send_chat_action(self, action, **kwargs):
        self.actions.append((action, dict(kwargs)))
        return True

    async def reply_photo(self, photo, caption=None, **kwargs):
        self.photos.append((photo, caption, dict(kwargs)))
        return True

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        self.documents.append((document, filename, caption, dict(kwargs)))
        return True


@pytest.mark.asyncio
async def test_handle_ai_chat_silently_ignores_unauthorized_user(monkeypatch):
    import core.config as config_module
    import core.heartbeat_store as heartbeat_module

    async def _deny_user(_user_id):
        return False

    async def _forbidden(*_args, **_kwargs):
        raise AssertionError("unauthorized request should short-circuit")

    monkeypatch.setattr(config_module, "is_user_allowed", _deny_user)
    monkeypatch.setattr(
        heartbeat_module.heartbeat_store,
        "set_delivery_target",
        _forbidden,
    )
    monkeypatch.setattr(ai_handlers, "add_message", _forbidden)

    ctx = _DummyChatContext()

    await ai_handlers.handle_ai_chat(ctx)

    assert not ctx.replies
    assert not ctx.edits
    assert not ctx.photos
    assert not ctx.documents


@pytest.mark.asyncio
async def test_handle_ai_chat_does_not_attach_plain_path_from_final_text(
    monkeypatch, tmp_path
):
    import core.config as config_module
    import core.heartbeat_store as heartbeat_module
    import core.task_manager as task_manager_module
    from core.agent_orchestrator import agent_orchestrator
    from handlers import message_utils

    image_path = (tmp_path / "catdog.png").resolve()
    image_path.write_bytes(b"png")

    async def _allow_user(_user_id):
        return True

    async def _noop(*_args, **_kwargs):
        return None

    async def _false(*_args, **_kwargs):
        return False

    async def _empty_history(*_args, **_kwargs):
        return []

    async def _fake_process_reply_message(_ctx):
        return False, "", None, ""

    async def _fake_get_user_settings(_uid):
        return {}

    async def _identity_process_code_files(_ctx, text):
        return text

    async def _fake_handle_message(_ctx, _message_history):
        yield "✅ 图片已成功生成！\n" f"文件路径: `{image_path}`\n" "请查收。"

    monkeypatch.setattr(config_module, "is_user_allowed", _allow_user)
    monkeypatch.setattr(
        heartbeat_module.heartbeat_store,
        "set_delivery_target",
        _noop,
    )
    monkeypatch.setattr(ai_handlers, "add_message", _noop)
    monkeypatch.setattr(ai_handlers, "increment_stat", _noop)
    monkeypatch.setattr(ai_handlers, "_try_handle_waiting_confirmation", _false)
    monkeypatch.setattr(ai_handlers, "_try_handle_memory_commands", _false)
    monkeypatch.setattr(ai_handlers, "get_user_settings", _fake_get_user_settings)
    monkeypatch.setattr(ai_handlers, "get_user_context", _empty_history)
    monkeypatch.setattr(
        ai_handlers, "process_and_send_code_files", _identity_process_code_files
    )
    monkeypatch.setattr(message_utils, "process_reply_message", _fake_process_reply_message)
    monkeypatch.setattr(agent_orchestrator, "handle_message", _fake_handle_message)
    monkeypatch.setattr(task_manager_module.task_manager, "register_task", _noop)
    monkeypatch.setattr(task_manager_module.task_manager, "is_cancelled", lambda _uid: False)
    monkeypatch.setattr(task_manager_module.task_manager, "unregister_task", lambda _uid: None)

    ctx = _DummyChatContext()

    await ai_handlers.handle_ai_chat(ctx)

    assert not ctx.photos


@pytest.mark.asyncio
async def test_handle_ai_chat_injects_recent_followup_context(monkeypatch):
    import core.config as config_module
    import core.heartbeat_store as heartbeat_module
    import core.task_manager as task_manager_module
    from core.agent_orchestrator import agent_orchestrator
    from handlers import message_utils

    captured: dict[str, str] = {}

    async def _allow_user(_user_id):
        return True

    async def _noop(*_args, **_kwargs):
        return None

    async def _false(*_args, **_kwargs):
        return False

    async def _empty_history(*_args, **_kwargs):
        return []

    async def _fake_process_reply_message(_ctx):
        return False, "", None, ""

    async def _fake_get_user_settings(_uid):
        return {}

    async def _identity_process_code_files(_ctx, text):
        return text

    class _FakeSessionTaskStore:
        async def get_active(self, _user_id: str):
            return None

        async def match_followup(self, _user_id: str, _user_message: str):
            return {
                "session_task_id": "tsk-prev-1",
                "context_text": (
                    "【任务续接上下文】\n"
                    "- 最近任务：`tsk-prev-1`\n"
                    "- 最近结果摘要：n8n 已关闭。\n"
                    "这句短回复是在承接上一条已完成任务。"
                ),
            }

    async def _fake_handle_message(_ctx, message_history):
        captured["last_user_text"] = str(message_history[-1]["parts"][0]["text"])
        yield "好的，我继续处理。"

    monkeypatch.setattr(config_module, "is_user_allowed", _allow_user)
    monkeypatch.setattr(heartbeat_module.heartbeat_store, "set_delivery_target", _noop)
    monkeypatch.setattr(ai_handlers, "add_message", _noop)
    monkeypatch.setattr(ai_handlers, "increment_stat", _noop)
    monkeypatch.setattr(ai_handlers, "_try_handle_waiting_confirmation", _false)
    monkeypatch.setattr(ai_handlers, "_try_handle_memory_commands", _false)
    monkeypatch.setattr(ai_handlers, "get_user_settings", _fake_get_user_settings)
    monkeypatch.setattr(ai_handlers, "get_user_context", _empty_history)
    monkeypatch.setattr(
        ai_handlers, "process_and_send_code_files", _identity_process_code_files
    )
    async def _fake_bind_followup(_ctx, _msg: str):
        return (
            "【任务续接上下文】\n"
            "- 最近任务：`tsk-prev-1`\n"
            "- 最近结果摘要：n8n 已关闭。\n"
            "这句短回复是在承接上一条已完成任务。"
        )

    monkeypatch.setattr(
        ai_handlers,
        "maybe_bind_recent_followup_context",
        _fake_bind_followup,
    )
    monkeypatch.setattr(message_utils, "process_reply_message", _fake_process_reply_message)
    monkeypatch.setattr(agent_orchestrator, "handle_message", _fake_handle_message)
    monkeypatch.setattr(task_manager_module.task_manager, "register_task", _noop)
    monkeypatch.setattr(task_manager_module.task_manager, "is_cancelled", lambda _uid: False)
    monkeypatch.setattr(task_manager_module.task_manager, "unregister_task", lambda _uid: None)

    ctx = _DummyChatContext()
    ctx.message.text = "需要"

    await ai_handlers.handle_ai_chat(ctx)

    assert "任务续接上下文" in captured["last_user_text"]
    assert "当前用户补充：需要" in captured["last_user_text"]
