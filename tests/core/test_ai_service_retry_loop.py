from types import SimpleNamespace
import os
import json

import pytest

os.environ.setdefault("LLM_API_KEY", "test-key")

from services.ai_service import AiService
import services.ai_service as ai_service_module


class _Response:
    def __init__(self, *, function_call=None, text=""):
        tool_calls = []
        if function_call is not None:
            tool_calls = [
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(
                        name=str(function_call.name),
                        arguments=json.dumps(
                            function_call.args or {}, ensure_ascii=False
                        ),
                    ),
                )
            ]
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(
                    content=text,
                    tool_calls=tool_calls,
                )
            )
        ]


class _FakeModels:
    def __init__(self):
        self.calls = 0
        self.responses = [
            _Response(
                function_call=SimpleNamespace(name="read", args={"path": "a.txt"})
            ),
            _Response(text="请补充更多信息。"),
            _Response(
                function_call=SimpleNamespace(name="read", args={"path": "a.txt"})
            ),
            _Response(text="任务完成"),
        ]

    async def create(self, **kwargs):
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class _FakeChat:
    def __init__(self):
        self.completions = _FakeModels()


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat()


@pytest.mark.asyncio
async def test_ai_service_retries_after_tool_failure(monkeypatch):
    service = AiService()
    fake_client = _FakeClient()
    monkeypatch.setattr(ai_service_module, "openai_async_client", fake_client)

    tool_calls = {"count": 0}

    async def tool_executor(_name, _args):
        tool_calls["count"] += 1
        if tool_calls["count"] == 1:
            return {"ok": False, "message": "failed_once"}
        return {"ok": True, "summary": "ok"}

    events = []

    async def event_callback(event, payload):
        events.append((event, payload))

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "执行任务"}]}],
        tools=[{"name": "read", "description": "", "parameters": {"type": "object"}}],
        tool_executor=tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert chunks == ["任务完成"]
    assert tool_calls["count"] == 2
    assert any(name == "retry_after_failure" for name, _ in events)


class _CaptureModels:
    def __init__(self):
        self.calls = 0
        self.seen_messages = []
        self.responses = [
            _Response(
                function_call=SimpleNamespace(name="read", args={"path": "a.txt"})
            ),
            _Response(text="继续补充"),
            _Response(text="完成"),
        ]

    async def create(self, **kwargs):
        self.seen_messages.append(kwargs.get("messages"))
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class _CaptureChat:
    def __init__(self):
        self.completions = _CaptureModels()


class _CaptureClient:
    def __init__(self):
        self.chat = _CaptureChat()


@pytest.mark.asyncio
async def test_ai_service_uses_retry_instruction_from_event_callback(monkeypatch):
    service = AiService()
    fake_client = _CaptureClient()
    monkeypatch.setattr(ai_service_module, "openai_async_client", fake_client)

    async def tool_executor(_name, _args):
        return {"ok": False, "message": "failed_once"}

    async def event_callback(event, _payload):
        if event == "retry_after_failure":
            return {"recovery_instruction": "阶段2指令：改用四原语兜底修复"}
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "执行任务"}]}],
        tools=[{"name": "read", "description": "", "parameters": {"type": "object"}}],
        tool_executor=tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert chunks == ["完成"]
    flattened_text = []
    for message in fake_client.chat.completions.seen_messages[-1]:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            flattened_text.append(content)

    assert any("阶段2指令" in text for text in flattened_text)


class _BinaryCaptureModels:
    def __init__(self):
        self.calls = 0
        self.seen_messages = []
        self.responses = [
            _Response(
                function_call=SimpleNamespace(name="ext_generate_image", args={}),
            ),
            _Response(text="图片已完成"),
        ]

    async def create(self, **kwargs):
        self.seen_messages.append(kwargs.get("messages"))
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class _BinaryCaptureChat:
    def __init__(self):
        self.completions = _BinaryCaptureModels()


class _BinaryCaptureClient:
    def __init__(self):
        self.chat = _BinaryCaptureChat()


@pytest.mark.asyncio
async def test_ai_service_sanitizes_binary_tool_result_for_history(monkeypatch):
    service = AiService()
    fake_client = _BinaryCaptureClient()
    monkeypatch.setattr(ai_service_module, "openai_async_client", fake_client)

    async def tool_executor(_name, _args):
        return {
            "ok": True,
            "text": "图片生成完成",
            "files": {"dog.png": b"\x89PNG\r\n\x1a\n"},
        }

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "画一只猫"}]}],
        tools=[
            {
                "name": "ext_generate_image",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=tool_executor,
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert chunks == ["图片已完成"]
    assert len(fake_client.chat.completions.seen_messages) >= 2
    second_turn = fake_client.chat.completions.seen_messages[1]
    tool_messages = [
        row
        for row in second_turn
        if isinstance(row, dict) and row.get("role") == "tool"
    ]
    assert tool_messages
    assert "dog.png" in str(tool_messages[-1].get("content") or "")
