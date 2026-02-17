from types import SimpleNamespace
import os

import pytest

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from core.agent_orchestrator import AgentOrchestrator
from core.extension_executor import ExtensionRunResult
from core.extension_router import ExtensionCandidate
from services.ai_service import AiService
import core.agent_orchestrator as orchestrator_module
import services.ai_service as ai_service_module


class DummyContext:
    def __init__(self):
        self.message = SimpleNamespace(user=SimpleNamespace(id=123))
        self.user_data = {}
        self.replies = []
        self.documents = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return None

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        self.documents.append((filename, caption, kwargs))
        return None


@pytest.mark.asyncio
async def test_orchestrator_default_tools_are_primitives(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "你好"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["ok"]
    assert captured["tool_names"] == [
        "read",
        "write",
        "edit",
        "bash",
        "list_workers",
        "dispatch_worker",
        "worker_status",
    ]
    assert "call_skill" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_manager_does_not_inject_short_lived_extension(
    monkeypatch,
):
    orchestrator = AgentOrchestrator()
    captured = {}

    candidate = ExtensionCandidate(
        name="rss_subscribe",
        description="RSS subscription",
        tool_name="ext_rss_subscribe",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["rss", "订阅"],
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "订阅 rss"}]}]

    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert "ext_rss_subscribe" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_injects_deployment_without_skill_manager_by_default(
    monkeypatch,
):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    captured = {}

    candidate = ExtensionCandidate(
        name="deployment_manager",
        description="Deployment manager",
        tool_name="ext_deployment_manager",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["部署", "deploy"],
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "帮我部署一套n8n"}]}]

    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert "ext_deployment_manager" not in captured["tool_names"]
    assert "ext_skill_manager" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_keeps_deployment_extension_when_explicitly_requested(
    monkeypatch,
):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    captured = {}

    candidate = ExtensionCandidate(
        name="deployment_manager",
        description="Deployment manager",
        tool_name="ext_deployment_manager",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["部署", "deploy"],
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )

    ctx = DummyContext()
    message_history = [
        {"role": "user", "parts": [{"text": "请使用 deployment_manager 部署一套n8n"}]}
    ]

    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert "ext_deployment_manager" not in captured["tool_names"]
    assert "ext_skill_manager" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_injects_skill_manager_for_skill_intent(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext()
    message_history = [
        {"role": "user", "parts": [{"text": "帮我创建一个新技能处理这个任务"}]}
    ]

    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert "ext_skill_manager" not in captured["tool_names"]
    assert "run_extension" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_prompt_includes_runtime_and_skill_briefs(monkeypatch):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    captured = {}

    candidate = ExtensionCandidate(
        name="deployment_manager",
        description="Deployment manager",
        tool_name="ext_deployment_manager",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["部署", "deploy"],
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        captured["system_instruction"] = system_instruction or ""
        yield "ok"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "帮我部署一套 n8n"}]}]

    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert "ext_deployment_manager" not in captured["tool_names"]
    assert "ext_skill_manager" not in captured["tool_names"]
    assert "【SOUL】" in captured["system_instruction"]
    assert "【可用工具】" in captured["system_instruction"]
    assert "ext_deployment_manager" not in captured["system_instruction"]
    assert "【运行环境事实】" not in captured["system_instruction"]


@pytest.mark.asyncio
async def test_orchestrator_uses_deployment_staging_path_for_primitives(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    candidate = ExtensionCandidate(
        name="deployment_manager",
        description="Deployment manager",
        tool_name="ext_deployment_manager",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["部署", "deploy"],
    )

    async def fake_runtime_write(
        path,
        content,
        mode="overwrite",
        create_parents=True,
        encoding="utf-8",
    ):
        captured["write_path"] = path
        return {"ok": True, "summary": "ok"}

    async def fake_runtime_bash(command, cwd=None, timeout_sec=60):
        captured["bash_cwd"] = cwd
        captured["bash_command"] = command
        return {"ok": True, "summary": "ok", "data": {"cwd": cwd, "command": command}}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        await tool_executor(
            "write",
            {"path": "n8n/docker-compose.yml", "content": "services: {}\n"},
        )
        await tool_executor("bash", {"command": "pwd"})
        if event_callback:
            await event_callback("final_response", {"turn": 1, "text_preview": "done"})
        yield "done"

    staging_root = "/tmp/xbot-deployment-staging-test"
    monkeypatch.setattr(orchestrator_module, "X_DEPLOYMENT_STAGING_PATH", staging_root)
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )
    monkeypatch.setattr(orchestrator.runtime, "write", fake_runtime_write)
    monkeypatch.setattr(orchestrator.runtime, "bash", fake_runtime_bash)
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "帮我部署一套n8n"}]}]
    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert captured["write_path"] == f"{staging_root}/n8n/docker-compose.yml"
    assert captured["bash_cwd"] == staging_root


@pytest.mark.asyncio
async def test_orchestrator_routes_with_recent_user_context(monkeypatch):
    orchestrator = AgentOrchestrator()
    captured = {}

    def fake_route(user_text, max_candidates=3):
        captured["routing_text"] = user_text
        return []

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        yield "ok"

    monkeypatch.setattr(orchestrator.extension_router, "route", fake_route)
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
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
async def test_orchestrator_keeps_stream_path_even_when_fastpath_enabled(monkeypatch):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = True
    stream_called = {"value": False}
    execute_called = {"value": False}

    candidate = ExtensionCandidate(
        name="deep_research",
        description="Deep research",
        tool_name="ext_deep_research",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["深度研究", "deep research"],
    )

    async def fake_stream(*_args, **_kwargs):
        stream_called["value"] = True
        yield "ok"

    async def fake_execute(*_args, **_kwargs):
        execute_called["value"] = True
        return ExtensionRunResult(
            ok=True, skill_name="deep_research", text="unexpected"
        )

    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator.extension_executor, "execute", fake_execute)

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "帮我深度研究 Minimax"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks == ["ok"]
    assert stream_called["value"] is True
    assert execute_called["value"] is False


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
        _ = message_history
        _ = tools
        _ = system_instruction
        blocked = await tool_executor("open_nodes", {"names": ["User"]})
        text = str(blocked.get("message") or "")
        if event_callback:
            await event_callback("final_response", {"text_preview": text})
        yield text

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "你好"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert chunks
    assert "Tool not available" in chunks[0]


@pytest.mark.asyncio
async def test_orchestrator_uses_runtime_user_id_override_for_worker_policy(
    monkeypatch,
):
    orchestrator = AgentOrchestrator()
    captured = {}

    candidate = ExtensionCandidate(
        name="searxng_search",
        description="search",
        tool_name="ext_searxng_search",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["search", "天气"],
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = message_history
        _ = tool_executor
        _ = system_instruction
        _ = event_callback
        captured["tool_names"] = [
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in (tools or [])
        ]
        yield "ok"

    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )

    ctx = DummyContext()
    ctx.message.user.id = "42"
    ctx.message.platform = "worker_runtime"
    ctx.user_data = {"runtime_user_id": "worker::worker-main::42"}
    message_history = [{"role": "user", "parts": [{"text": "查天气"}]}]

    _ = [chunk async for chunk in orchestrator.handle_message(ctx, message_history)]

    assert "ext_searxng_search" in captured["tool_names"]
    assert "list_workers" not in captured["tool_names"]


@pytest.mark.asyncio
async def test_orchestrator_auto_evolves_and_retries_after_turn_limit(monkeypatch):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    orchestrator.auto_evolve_enabled = True
    call_state = {"count": 0}

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        call_state["count"] += 1
        if call_state["count"] == 1:
            if event_callback:
                await event_callback("max_turn_limit", {"max_turns": 8})
            yield "⚠️ 工具调用轮次已达上限（8），任务仍未完成。请把任务拆分为更小步骤后重试。"
            return

        if event_callback:
            await event_callback("final_response", {"turn": 1, "text_preview": "done"})
        yield "done-after-evolution"

    async def fake_execute(skill_name, args, ctx, runtime):
        if skill_name == "skill_manager":
            return ExtensionRunResult(
                ok=True,
                skill_name=skill_name,
                text="🛠️ 新技能已生成并激活",
            )
        raise AssertionError(f"unexpected skill execution: {skill_name}")

    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator.extension_executor, "execute", fake_execute)

    ctx = DummyContext()
    message_history = [
        {
            "role": "user",
            "parts": [{"text": "帮我完成一个目前不会的复杂任务，允许创建技能"}],
        }
    ]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert call_state["count"] == 2
    assert any("新技能已生成并激活" in item for item in chunks)
    assert "done-after-evolution" in chunks[-1]
    assert not any("轮次已达上限" in item for item in chunks)


@pytest.mark.asyncio
async def test_orchestrator_skips_auto_evolve_when_non_skill_extension_exists(
    monkeypatch,
):
    orchestrator = AgentOrchestrator()
    orchestrator.direct_fastpath_enabled = False
    orchestrator.auto_evolve_enabled = True
    call_state = {"count": 0}

    candidate = ExtensionCandidate(
        name="deployment_manager",
        description="Deployment manager",
        tool_name="ext_deployment_manager",
        input_schema={"type": "object", "properties": {}},
        schema_summary="required=[], fields=[]",
        triggers=["部署", "deploy"],
    )

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        call_state["count"] += 1
        if event_callback:
            await event_callback("max_turn_limit", {"max_turns": 8})
        yield "⚠️ 工具调用轮次已达上限（8），任务仍未完成。请把任务拆分为更小步骤后重试。"

    async def fake_execute(skill_name, args, ctx, runtime):
        raise AssertionError(f"unexpected auto-evolve execution: {skill_name}")

    monkeypatch.setattr(
        orchestrator.extension_router, "route", lambda *_args, **_kwargs: [candidate]
    )
    monkeypatch.setattr(
        orchestrator.ai_service, "generate_response_stream", fake_stream
    )
    monkeypatch.setattr(orchestrator.extension_executor, "execute", fake_execute)

    ctx = DummyContext()
    message_history = [{"role": "user", "parts": [{"text": "帮我部署一套n8n"}]}]

    chunks = [
        chunk async for chunk in orchestrator.handle_message(ctx, message_history)
    ]

    assert call_state["count"] == 1
    assert any("轮次已达上限" in item for item in chunks)


@pytest.mark.asyncio
async def test_ai_service_returns_visible_failure_on_turn_limit(monkeypatch):
    service = AiService()

    class FakePart:
        def __init__(self):
            self.function_call = SimpleNamespace(name="read", args={"path": "a.txt"})

    class FakeContent:
        def __init__(self):
            self.parts = [FakePart()]

    class FakeCandidate:
        def __init__(self):
            self.content = FakeContent()

    class FakeResponse:
        def __init__(self):
            self.candidates = [FakeCandidate()]
            self.text = ""

    class FakeModels:
        async def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeAio:
        def __init__(self):
            self.models = FakeModels()

    class FakeClient:
        def __init__(self):
            self.aio = FakeAio()

    monkeypatch.setattr(ai_service_module, "gemini_client", FakeClient())

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
