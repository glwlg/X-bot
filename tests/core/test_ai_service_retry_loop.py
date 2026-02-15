from types import SimpleNamespace
import os

import pytest

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from services.ai_service import AiService
import services.ai_service as ai_service_module


class _Part:
    def __init__(self, function_call=None):
        self.function_call = function_call


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Candidate:
    def __init__(self, content):
        self.content = content


class _Response:
    def __init__(self, *, function_call=None, text=""):
        parts = []
        if function_call is not None:
            parts = [_Part(function_call=function_call)]
        self.candidates = [_Candidate(_Content(parts))]
        self.text = text


class _FakeModels:
    def __init__(self):
        self.calls = 0
        self.responses = [
            _Response(function_call=SimpleNamespace(name="read", args={"path": "a.txt"})),
            _Response(text="请补充更多信息。"),
            _Response(function_call=SimpleNamespace(name="read", args={"path": "a.txt"})),
            _Response(text="任务完成"),
        ]

    async def generate_content(self, **kwargs):
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class _FakeAio:
    def __init__(self):
        self.models = _FakeModels()


class _FakeClient:
    def __init__(self):
        self.aio = _FakeAio()


@pytest.mark.asyncio
async def test_ai_service_retries_after_tool_failure(monkeypatch):
    service = AiService()
    fake_client = _FakeClient()
    monkeypatch.setattr(ai_service_module, "gemini_client", fake_client)

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
        self.seen_contents = []
        self.responses = [
            _Response(function_call=SimpleNamespace(name="read", args={"path": "a.txt"})),
            _Response(text="继续补充"),
            _Response(text="完成"),
        ]

    async def generate_content(self, **kwargs):
        self.seen_contents.append(kwargs.get("contents"))
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class _CaptureAio:
    def __init__(self):
        self.models = _CaptureModels()


class _CaptureClient:
    def __init__(self):
        self.aio = _CaptureAio()


@pytest.mark.asyncio
async def test_ai_service_uses_retry_instruction_from_event_callback(monkeypatch):
    service = AiService()
    fake_client = _CaptureClient()
    monkeypatch.setattr(ai_service_module, "gemini_client", fake_client)

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
    for content in fake_client.aio.models.seen_contents[-1]:
        if isinstance(content, dict):
            parts = content.get("parts") or []
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    flattened_text.append(str(part["text"]))
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", "")
            if text:
                flattened_text.append(str(text))

    assert any("阶段2指令" in text for text in flattened_text)
