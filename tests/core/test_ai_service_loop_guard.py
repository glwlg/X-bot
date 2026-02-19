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
