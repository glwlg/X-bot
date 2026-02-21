from types import SimpleNamespace
import json

import pytest

import services.ai_service as ai_service_module
from services.ai_service import AiService


@pytest.mark.asyncio
async def test_ai_service_emits_loop_guard_on_repeated_calls(monkeypatch):
    service = AiService()
    events = []

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
            return FakeResponse()

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "2")

    async def fake_tool_executor(_name, _args):
        return {"ok": True}

    async def event_callback(event, payload):
        events.append(event)

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "test"}]}],
        tools=[{"name": "read", "description": "", "parameters": {"type": "object"}}],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert "loop_guard" in events
    assert chunks
    assert "重复工具调用" in chunks[-1]


@pytest.mark.asyncio
async def test_ai_service_keeps_full_terminal_text_without_truncation(monkeypatch):
    service = AiService()
    long_text = "✅DEPLOY_OK\n" + ("x" * 1200)
    captured_terminal = {"value": ""}

    class FakeResponse:
        def __init__(self):
            function_call = SimpleNamespace(
                name="ext_deployment_manager",
                args={"action": "auto_deploy"},
            )
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name=function_call.name,
                                    arguments=json.dumps(
                                        function_call.args,
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
            return FakeResponse()

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())

    async def fake_tool_executor(_name, _args):
        return {
            "ok": True,
            "terminal": True,
            "task_outcome": "done",
            "text": long_text,
        }

    async def event_callback(event, payload):
        if event == "tool_call_finished":
            captured_terminal["value"] = payload.get("terminal_text", "")
            return {"stop": True, "final_text": payload.get("terminal_text", "")}
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "deploy"}]}],
        tools=[
            {
                "name": "ext_deployment_manager",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert captured_terminal["value"] == long_text
    assert "".join(chunks) == long_text
    assert len("".join(chunks)) > 500


@pytest.mark.asyncio
async def test_ai_service_terminal_payload_uses_result_and_ui(monkeypatch):
    service = AiService()
    terminal_result = "✅RSS_READY\n" + ("a" * 600)
    captured = {"text": "", "ui": {}}

    class FakeResponse:
        def __init__(self):
            function_call = SimpleNamespace(
                name="dispatch_worker",
                args={"instruction": "检查 RSS"},
            )
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name=function_call.name,
                                    arguments=json.dumps(
                                        function_call.args,
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
            return FakeResponse()

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())

    async def fake_tool_executor(_name, _args):
        return {
            "ok": True,
            "terminal": True,
            "task_outcome": "done",
            "result": terminal_result,
            "ui": {
                "actions": [
                    [{"text": "刷新", "callback_data": "rss_refresh"}],
                ]
            },
        }

    async def event_callback(event, payload):
        if event == "tool_call_finished":
            captured["text"] = str(payload.get("terminal_text") or "")
            captured["ui"] = payload.get("terminal_ui") or {}
            return {"stop": True, "final_text": payload.get("terminal_text", "")}
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "rss"}]}],
        tools=[
            {
                "name": "dispatch_worker",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert chunks[-1] == terminal_result
    assert captured["text"] == terminal_result
    assert captured["ui"].get("actions")


@pytest.mark.asyncio
async def test_ai_service_stops_when_tool_call_budget_exceeded(monkeypatch):
    service = AiService()

    class FakeResponse:
        def __init__(self):
            function_call = SimpleNamespace(
                name="ext_web_search",
                args={"queries": ["上海 天气"]},
            )
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name=function_call.name,
                                    arguments=json.dumps(
                                        function_call.args,
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
            return FakeResponse()

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())
    monkeypatch.setenv("AI_TOOL_MAX_CALLS_PER_TOOL", "2")
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "99")
    monkeypatch.setenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "99")

    calls = {"count": 0}

    async def fake_tool_executor(_name, _args):
        calls["count"] += 1
        return {"ok": True, "summary": "ok"}

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "查天气"}]}],
        tools=[
            {
                "name": "ext_web_search",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert calls["count"] == 2
    assert chunks
    assert "单工具调用上限" in chunks[-1]


@pytest.mark.asyncio
async def test_ai_service_budget_guard_synthesizes_final_answer(monkeypatch):
    service = AiService()
    events = []

    class FakeModels:
        async def create(self, **kwargs):
            if kwargs.get("tools"):
                function_call = SimpleNamespace(
                    name="ext_web_search",
                    args={"queries": ["无锡 明天 天气"]},
                )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name=function_call.name,
                                            arguments=json.dumps(
                                                function_call.args,
                                                ensure_ascii=False,
                                            ),
                                        ),
                                    )
                                ],
                            )
                        )
                    ]
                )

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="已基于现有信息整理结论。",
                            tool_calls=[],
                        )
                    )
                ]
            )

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())
    monkeypatch.setenv("AI_TOOL_MAX_CALLS_PER_TOOL", "1")
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "99")
    monkeypatch.setenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "99")

    calls = {"count": 0}

    async def fake_tool_executor(_name, _args):
        calls["count"] += 1
        return {"ok": True, "summary": "ok"}

    async def event_callback(event, payload):
        events.append((event, payload))

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "查天气"}]}],
        tools=[
            {
                "name": "ext_web_search",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert calls["count"] == 1
    assert "".join(chunks) == "已基于现有信息整理结论。"
    assert all("单工具调用上限" not in chunk for chunk in chunks)
    assert any(name == "tool_budget_guard" for name, _ in events)
    assert any(
        name == "final_response" and payload.get("source") == "tool_budget_guard"
        for name, payload in events
    )


@pytest.mark.asyncio
async def test_ai_service_stops_on_semantic_repeat_calls(monkeypatch):
    service = AiService()

    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            variants = [
                {"queries": ["https://example.com 上海 天气"]},
                {"queries": ["example.com   上海   天气"]},
                {"queries": ["EXAMPLE.COM 上海 天气"]},
            ]
            idx = min(self.calls, len(variants) - 1)
            self.calls += 1
            args = variants[idx]
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[
                                SimpleNamespace(
                                    id=f"call-{self.calls}",
                                    function=SimpleNamespace(
                                        name="ext_web_browser",
                                        arguments=json.dumps(args, ensure_ascii=False),
                                    ),
                                )
                            ],
                        )
                    )
                ]
            )

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "99")
    monkeypatch.setenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "2")

    async def fake_tool_executor(_name, _args):
        return {"ok": True, "summary": "ok"}

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "查天气"}]}],
        tools=[
            {
                "name": "ext_web_browser",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert chunks
    assert "语义上重复" in chunks[-1]


@pytest.mark.asyncio
async def test_ai_service_cost_guards_skip_non_extension_tools(monkeypatch):
    service = AiService()

    class FakeModels:
        def __init__(self):
            self.calls = 0
            self.responses = [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name="read",
                                            arguments=json.dumps(
                                                {"path": "a.txt"},
                                                ensure_ascii=False,
                                            ),
                                        ),
                                    )
                                ],
                            )
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-2",
                                        function=SimpleNamespace(
                                            name="read",
                                            arguments=json.dumps(
                                                {"path": "a.txt"},
                                                ensure_ascii=False,
                                            ),
                                        ),
                                    )
                                ],
                            )
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="done",
                                tool_calls=[],
                            )
                        )
                    ]
                ),
            ]

        async def create(self, **kwargs):
            idx = min(self.calls, len(self.responses) - 1)
            self.calls += 1
            return self.responses[idx]

    class FakeChat:
        def __init__(self):
            self.completions = FakeModels()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())
    monkeypatch.setenv("AI_TOOL_MAX_CALLS_PER_TOOL", "1")
    monkeypatch.setenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "2")
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "99")

    call_count = {"value": 0}

    async def fake_tool_executor(_name, _args):
        call_count["value"] += 1
        return {"ok": True, "summary": "ok"}

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "read twice"}]}],
        tools=[
            {
                "name": "read",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert call_count["value"] == 2
    assert chunks == ["done"]


@pytest.mark.asyncio
async def test_ai_service_async_dispatch_emits_progress_notice(monkeypatch):
    service = AiService()
    events = []

    class FakeModels:
        def __init__(self):
            self.dispatch_calls = 0
            self.notice_calls = 0

        async def create(self, **kwargs):
            if kwargs.get("tools"):
                self.dispatch_calls += 1
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name="dispatch_worker",
                                            arguments=json.dumps(
                                                {"instruction": "查询明天天气"},
                                                ensure_ascii=False,
                                            ),
                                        ),
                                    )
                                ],
                            )
                        )
                    ]
                )

            self.notice_calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "已派发给阿黑处理（任务 wj-1），"
                                "正在处理中，完成后会自动把结果发给你。"
                            ),
                            tool_calls=[],
                        )
                    )
                ]
            )

    fake_models = FakeModels()

    class FakeChat:
        def __init__(self):
            self.completions = fake_models

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(ai_service_module, "openai_async_client", FakeClient())

    calls = {"count": 0}

    async def fake_tool_executor(_name, _args):
        calls["count"] += 1
        return {
            "ok": True,
            "async_dispatch": True,
            "worker_name": "阿黑",
            "task_id": "wj-1",
            "task_outcome": "partial",
            "text": "worker dispatch accepted",
            "summary": "worker job queued",
        }

    async def event_callback(event, payload):
        events.append((event, payload))

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "查天气"}]}],
        tools=[
            {
                "name": "dispatch_worker",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert calls["count"] == 1
    assert fake_models.dispatch_calls == 1
    assert fake_models.notice_calls == 1
    text = "".join(chunks)
    assert "任务 wj-1" in text
    assert "完成后会自动把结果发给你" in text
    assert any(
        name == "final_response" and payload.get("source") == "async_dispatch"
        for name, payload in events
    )
