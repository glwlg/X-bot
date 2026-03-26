from types import SimpleNamespace
import json
import os

import pytest

os.environ.setdefault("LLM_API_KEY", "test-key")

from core.agent_orchestrator import AgentOrchestrator
from core.extension_router import ExtensionCandidate
from services.ai_service import AiService
from services.intent_router import RoutingDecision
import services.ai_service as ai_service_module


class DummyContext:
    def __init__(self):
        self.message = SimpleNamespace(
            user=SimpleNamespace(id="123"),
            platform="telegram",
            chat=SimpleNamespace(id="chat-1"),
        )
        self.user_data = {}
        self.replies = []
        self.documents = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return None

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        _ = document
        self.documents.append((filename, caption, kwargs))
        return None


@pytest.mark.asyncio
async def test_orchestrator_ikaros_tool_surface_matches_current_runtime(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, tool_executor, system_instruction, event_callback)
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    async def fake_route(**_kwargs):
        return RoutingDecision(
            request_mode="chat",
            candidate_skills=[],
            reason="chat",
            confidence=0.9,
        )

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator, "_runtime_tool_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr("core.agent_orchestrator.intent_router.route", fake_route)

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "你好"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["ok"]
    assert set(captured["tool_names"]) == {
        "read",
        "write",
        "edit",
        "bash",
        "await_subagents",
        "codex_session",
        "git_ops",
        "gh_cli",
        "repo_workspace",
        "send_local_file",
        "spawn_subagent",
        "task_tracker",
    }
    assert "load_skill" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_intent_router_narrows_prompt_and_load_skill(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, tool_executor, system_instruction, event_callback)
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    async def fake_route(**_kwargs):
        return RoutingDecision(
            request_mode="task",
            candidate_skills=["web_search"],
            reason="match",
            confidence=0.9,
        )

    def fake_compose_base(**kwargs):
        captured["allowed_skill_names"] = list(kwargs.get("allowed_skill_names") or [])
        return "system"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator, "_runtime_tool_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(
        orchestrator.extension_router,
        "route",
        lambda *_args, **_kwargs: [
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
        ],
    )
    monkeypatch.setattr("core.agent_orchestrator.intent_router.route", fake_route)
    monkeypatch.setattr(
        "core.agent_orchestrator.prompt_composer.compose_base", fake_compose_base
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "查一下这个网页"}]}]
    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert captured["allowed_skill_names"] == ["web_search"]
    assert "load_skill" in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_intent_router_empty_result_removes_load_skill(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, tool_executor, system_instruction, event_callback)
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    async def fake_route(**_kwargs):
        return RoutingDecision(
            request_mode="chat",
            candidate_skills=[],
            reason="none",
            confidence=0.7,
        )

    def fake_compose_base(**kwargs):
        captured["allowed_skill_names"] = list(kwargs.get("allowed_skill_names") or [])
        return "system"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator, "_runtime_tool_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(
        orchestrator.extension_router,
        "route",
        lambda *_args, **_kwargs: [
            ExtensionCandidate(
                name="web_search",
                description="网页搜索",
                tool_name="ext_web_search",
            )
        ],
    )
    monkeypatch.setattr("core.agent_orchestrator.intent_router.route", fake_route)
    monkeypatch.setattr(
        "core.agent_orchestrator.prompt_composer.compose_base", fake_compose_base
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "你好"}]}]
    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert captured["allowed_skill_names"] == []
    assert "load_skill" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_chat_mode_skips_task_tracking(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {"task_submit": 0, "session_activate": 0}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, tools, tool_executor, system_instruction, event_callback)
        yield "ok"

    async def fake_route(**_kwargs):
        return RoutingDecision(
            request_mode="chat",
            candidate_skills=[],
            reason="chat",
            confidence=0.9,
        )

    async def fake_submit(**_kwargs):
        captured["task_submit"] += 1
        return SimpleNamespace(task_id="inbox-1")

    async def fake_activate_session(_self, **_kwargs):
        captured["session_activate"] += 1
        return None

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator, "_runtime_tool_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr("core.agent_orchestrator.intent_router.route", fake_route)
    monkeypatch.setattr("core.agent_orchestrator.task_inbox.submit", fake_submit)
    monkeypatch.setattr(
        "core.agent_orchestrator.OrchestratorRuntimeContext.activate_session",
        fake_activate_session,
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "我们来玩猜人物"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["ok"]
    assert captured["task_submit"] == 0
    assert captured["session_activate"] == 0


@pytest.mark.asyncio
async def test_orchestrator_task_mode_without_tracking_skips_task_tracking(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {"task_submit": 0, "session_activate": 0}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, tools, tool_executor, system_instruction, event_callback)
        yield "ok"

    async def fake_route(**_kwargs):
        return RoutingDecision(
            request_mode="task",
            task_tracking=False,
            candidate_skills=["web_search"],
            reason="one_shot_lookup",
            confidence=0.88,
        )

    async def fake_submit(**_kwargs):
        captured["task_submit"] += 1
        return SimpleNamespace(task_id="inbox-1")

    async def fake_activate_session(_self, **_kwargs):
        captured["session_activate"] += 1
        return None

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator, "_runtime_tool_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr("core.agent_orchestrator.intent_router.route", fake_route)
    monkeypatch.setattr("core.agent_orchestrator.task_inbox.submit", fake_submit)
    monkeypatch.setattr(
        "core.agent_orchestrator.OrchestratorRuntimeContext.activate_session",
        fake_activate_session,
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "帮我总结这个仓库"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["ok"]
    assert captured["task_submit"] == 0
    assert captured["session_activate"] == 0


def test_resolve_task_workspace_root_uses_selected_ops_candidate(tmp_path, monkeypatch):
    orchestrator = AgentOrchestrator()
    monkeypatch.setattr("core.agent_orchestrator.X_DEPLOYMENT_STAGING_PATH", str(tmp_path))

    resolved = orchestrator._resolve_task_workspace_root(
        [
            ExtensionCandidate(
                name="deployment_manager",
                description="部署管理",
                tool_name="ext_deployment_manager",
            )
        ]
    )

    assert resolved == str(tmp_path.resolve())


def test_resolve_task_workspace_root_skips_non_ops_candidate(tmp_path, monkeypatch):
    orchestrator = AgentOrchestrator()
    monkeypatch.setattr("core.agent_orchestrator.X_DEPLOYMENT_STAGING_PATH", str(tmp_path))

    resolved = orchestrator._resolve_task_workspace_root(
        [
            ExtensionCandidate(
                name="web_search",
                description="网页搜索",
                tool_name="ext_web_search",
            )
        ]
    )

    assert resolved == ""


def test_should_auto_evolve_uses_selected_skill_groups():
    orchestrator = AgentOrchestrator()
    orchestrator.auto_evolve_enabled = True

    assert (
        orchestrator._should_auto_evolve(
            intent_text="随便写点什么",
            extension_candidates=[
                ExtensionCandidate(
                    name="skill_manager",
                    description="技能治理",
                    tool_name="ext_skill_manager",
                )
            ],
        )
        is True
    )
    assert (
        orchestrator._should_auto_evolve(
            intent_text="继续",
            extension_candidates=[
                ExtensionCandidate(
                    name="web_search",
                    description="网页搜索",
                    tool_name="ext_web_search",
                )
            ],
        )
        is False
    )


@pytest.mark.asyncio
async def test_orchestrator_routes_with_recent_user_context(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    def fake_route(user_text, max_candidates=3):
        _ = max_candidates
        captured["routing_text"] = user_text
        return []

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, tools, tool_executor, system_instruction, event_callback)
        yield "ok"

    async def fake_intent_route(**_kwargs):
        return RoutingDecision(
            request_mode="task",
            candidate_skills=[],
            reason="task",
            confidence=0.9,
        )

    monkeypatch.setattr(orchestrator.extension_router, "route", fake_route)
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        "core.agent_orchestrator.intent_router.route", fake_intent_route
    )

    ctx = DummyContext()
    message_history = [
        {"role": "user", "parts": [{"text": "帮我做深度研究 MiniMax 2.5"}]},
        {"role": "model", "parts": [{"text": "请补充你关心的维度"}]},
        {"role": "user", "parts": [{"text": "我指的是 minimax 2.5 最新发布"}]},
    ]

    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert "深度研究" in captured["routing_text"]
    assert "最新发布" in captured["routing_text"]


@pytest.mark.asyncio
async def test_orchestrator_rejects_non_injected_tool_call(monkeypatch):
    orchestrator = AgentOrchestrator()

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, tools, system_instruction)
        blocked = await tool_executor("open_nodes", {"names": ["User"]})
        text = str(blocked.get("message") or "")
        if event_callback:
            await event_callback("final_response", {"text_preview": text})
        yield text

    async def fake_route(**_kwargs):
        return RoutingDecision(
            request_mode="chat",
            candidate_skills=[],
            reason="chat",
            confidence=0.9,
        )

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr("core.agent_orchestrator.intent_router.route", fake_route)

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "你好"}]}]
    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks
    assert "Tool not available" in chunks[0]


@pytest.mark.asyncio
async def test_ai_service_returns_visible_failure_on_turn_limit(monkeypatch):
    service = AiService()

    class FakePart:
        def __init__(self):
            self.function_call = SimpleNamespace(name="read", args={"path": "a.txt"})

    class FakeResponse:
        def __init__(self):
            part = FakePart()
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name=part.function_call.name,
                                    arguments=json.dumps(
                                        part.function_call.args,
                                        ensure_ascii=False,
                                    ),
                                ),
                            )
                        ],
                    )
                )
            ]

    class FakeModels:
        async def create(self, **kwargs):
            _ = kwargs
            return FakeResponse()

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())

    async def fake_tool_executor(_name, _args):
        return {"ok": True}

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "test"}]}],
        tools=[{"name": "read", "description": "", "parameters": {"type": "object"}}],
        tool_executor=fake_tool_executor,
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert chunks
    assert ("轮次已达上限" in chunks[-1]) or ("重复工具调用" in chunks[-1])
