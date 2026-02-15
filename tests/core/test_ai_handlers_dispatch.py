from types import SimpleNamespace

import pytest

from handlers import ai_handlers


def test_resolve_worker_delegate_mode_keeps_worker_modes_for_task():
    assert ai_handlers._resolve_worker_delegate_mode("worker_only", True) == "worker_only"
    assert ai_handlers._resolve_worker_delegate_mode("worker_preferred", True) == "worker_preferred"
    assert ai_handlers._resolve_worker_delegate_mode("orchestrator", True) == "worker_only"


def test_resolve_worker_delegate_mode_skips_chat():
    assert ai_handlers._resolve_worker_delegate_mode("worker_only", False) == ""
    assert ai_handlers._resolve_worker_delegate_mode("worker_preferred", False) == ""
    assert ai_handlers._resolve_worker_delegate_mode("orchestrator", False) == ""


def test_is_worker_status_query():
    assert ai_handlers._is_worker_status_query("现在进度如何") is True
    assert ai_handlers._is_worker_status_query("status?") is True
    assert ai_handlers._is_worker_status_query("好了吗") is True
    assert ai_handlers._is_worker_status_query("你是谁") is False


@pytest.mark.asyncio
async def test_classify_task_intent_uses_model_result(monkeypatch):
    async def _fake_route(_text: str):
        return SimpleNamespace(route="worker_task", confidence=0.82, reason="model_task")

    monkeypatch.setattr(ai_handlers.intent_router, "route", _fake_route)
    is_task, conf, reason = await ai_handlers._classify_task_intent("今天有什么新闻")

    assert is_task is True
    assert conf == 0.82
    assert reason == "model_task"


@pytest.mark.asyncio
async def test_classify_task_intent_shell_fallback(monkeypatch):
    async def _fake_route(_text: str):
        return SimpleNamespace(route="manager_chat", confidence=0.2, reason="uncertain")

    monkeypatch.setattr(ai_handlers.intent_router, "route", _fake_route)
    is_task, conf, reason = await ai_handlers._classify_task_intent("docker compose ps")

    assert is_task is True
    assert conf >= 0.6
    assert reason == "shell_command_fallback"


@pytest.mark.asyncio
async def test_classify_task_intent_ambiguous_defaults_to_task(monkeypatch):
    async def _fake_route(_text: str):
        return SimpleNamespace(route="manager_chat", confidence=0.55, reason="ambiguous")

    monkeypatch.setattr(ai_handlers.intent_router, "route", _fake_route)
    is_task, conf, reason = await ai_handlers._classify_task_intent("今天有什么有意思的新闻")

    assert is_task is True
    assert conf >= 0.6
    assert reason in {"ambiguous", "ambiguous_chat_upgraded_to_task"}


@pytest.mark.asyncio
async def test_classify_task_intent_short_chat_stays_local(monkeypatch):
    async def _fake_route(_text: str):
        return SimpleNamespace(route="manager_chat", confidence=0.86, reason="small_talk")

    monkeypatch.setattr(ai_handlers.intent_router, "route", _fake_route)
    is_task, conf, reason = await ai_handlers._classify_task_intent("你好")

    assert is_task is False
    assert conf == 0.86
    assert reason == "small_talk"


@pytest.mark.asyncio
async def test_classify_task_intent_memory_route_stays_local(monkeypatch):
    async def _fake_route(_text: str):
        return SimpleNamespace(route="manager_memory", confidence=0.91, reason="memory_query")

    monkeypatch.setattr(ai_handlers.intent_router, "route", _fake_route)
    is_task, conf, reason = await ai_handlers._classify_task_intent("你知道我住在哪里吗")

    assert is_task is False
    assert conf == 0.91
    assert reason == "memory_query"


def test_render_user_memory_snapshot_from_relations():
    graph = {
        "entities": [
            {
                "name": "江苏无锡",
                "entityType": "location",
                "observations": ["由用户提供的地点信息：江苏无锡"],
            }
        ],
        "relations": [
            {
                "from": "User",
                "to": "江苏无锡",
                "relationType": "lives in",
            }
        ],
    }
    rendered = ai_handlers._render_user_memory_snapshot(graph)
    assert "居住地：江苏无锡" in rendered
    assert ai_handlers._graph_has_entity(graph, "User") is False
    inferred = ai_handlers._infer_user_observations_from_graph(graph)
    assert "居住地：江苏无锡" in inferred


@pytest.mark.asyncio
async def test_fetch_user_memory_snapshot_falls_back_to_read_graph(monkeypatch):
    class _FakeServer:
        async def call_tool(self, name, _args):
            if name == "open_nodes":
                return [SimpleNamespace(text='{"entities":[],"relations":[]}')]
            if name == "read_graph":
                return [
                    SimpleNamespace(
                        text=(
                            '{"entities":[{"name":"江苏无锡","entityType":"location","observations":["由用户提供的地点信息：江苏无锡"]}],'
                            '"relations":[{"from":"User","to":"江苏无锡","relationType":"lives in"}]}'
                        )
                    )
                ]
            raise AssertionError(f"unexpected tool call: {name}")

    async def _fake_get_memory_server_for_user(_uid: str):
        return _FakeServer()

    monkeypatch.setattr(ai_handlers, "_get_memory_server_for_user", _fake_get_memory_server_for_user)
    rendered = await ai_handlers._fetch_user_memory_snapshot("u-1")
    assert "居住地：江苏无锡" in rendered


@pytest.mark.asyncio
async def test_should_include_memory_summary_for_task_short_request():
    assert await ai_handlers._should_include_memory_summary_for_task("我住哪", "") is True


@pytest.mark.asyncio
async def test_build_worker_instruction_with_context_uses_manager_memory(monkeypatch):
    async def _fake_collect(_ctx, *, user_id, current_user_message, max_messages=6, max_chars=1200):
        del user_id, current_user_message, max_messages, max_chars
        return "- 用户: 上次让你记住我住在江苏无锡"

    async def _fake_fetch(_uid: str):
        return "- 居住地：江苏无锡"

    async def _fake_need_memory(_msg: str, _ctx: str) -> bool:
        return True

    monkeypatch.setattr(ai_handlers, "_collect_recent_dialog_context", _fake_collect)
    monkeypatch.setattr(ai_handlers, "_fetch_user_memory_snapshot", _fake_fetch)
    monkeypatch.setattr(ai_handlers, "_should_include_memory_summary_for_task", _fake_need_memory)

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
async def test_build_worker_instruction_with_context_skips_memory_when_worker_has_it(monkeypatch):
    async def _fake_collect(_ctx, *, user_id, current_user_message, max_messages=6, max_chars=1200):
        del user_id, current_user_message, max_messages, max_chars
        return ""

    async def _fake_fetch(_uid: str):
        raise AssertionError("should not fetch memory when worker_has_memory=True")

    async def _fake_need_memory(_msg: str, _ctx: str) -> bool:
        return True

    monkeypatch.setattr(ai_handlers, "_collect_recent_dialog_context", _fake_collect)
    monkeypatch.setattr(ai_handlers, "_fetch_user_memory_snapshot", _fake_fetch)
    monkeypatch.setattr(ai_handlers, "_should_include_memory_summary_for_task", _fake_need_memory)

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
async def test_build_worker_instruction_with_context_skips_memory_when_not_needed(monkeypatch):
    async def _fake_collect(_ctx, *, user_id, current_user_message, max_messages=6, max_chars=1200):
        del user_id, current_user_message, max_messages, max_chars
        return "- 用户: 请部署仓库"

    async def _fake_need_memory(_msg: str, _ctx: str) -> bool:
        return False

    async def _fake_fetch(_uid: str):
        raise AssertionError("should not fetch memory when not needed")

    monkeypatch.setattr(ai_handlers, "_collect_recent_dialog_context", _fake_collect)
    monkeypatch.setattr(ai_handlers, "_fetch_user_memory_snapshot", _fake_fetch)
    monkeypatch.setattr(ai_handlers, "_should_include_memory_summary_for_task", _fake_need_memory)

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
