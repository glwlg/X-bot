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


class _ModelResponseCompletions:
    def __init__(self, *, text="", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return _Response(text=self.text)


class _ModelResponseClient:
    def __init__(self, *, text="", error: Exception | None = None):
        self.chat = SimpleNamespace(
            completions=_ModelResponseCompletions(text=text, error=error)
        )


@pytest.mark.asyncio
async def test_ai_service_fails_over_to_backup_model_on_request_error(monkeypatch):
    service = AiService()
    primary_client = _ModelResponseClient(
        error=RuntimeError("Error code: 402 - {'detail': {'code': 'deactivated_workspace'}}")
    )
    backup_client = _ModelResponseClient(text="来自备用模型的回复")
    clients = {
        "proxy/gpt-5.4": primary_client,
        "proxy/bailian/qwen3.5-flash": backup_client,
    }

    monkeypatch.setattr(ai_service_module, "openai_async_client", None)
    monkeypatch.setattr(
        ai_service_module,
        "_resolve_async_client",
        lambda model_name: clients.get(str(model_name)),
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_for_input",
        lambda input_type, pool_type="primary": "proxy/gpt-5.4",
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_candidates_for_input",
        lambda *args, **kwargs: [
            "proxy/gpt-5.4",
            "proxy/bailian/qwen3.5-flash",
        ],
    )

    failed_models = []
    succeeded_models = []
    monkeypatch.setattr(
        ai_service_module, "mark_model_failed", lambda model_key: failed_models.append(model_key)
    )
    monkeypatch.setattr(
        ai_service_module,
        "mark_model_success",
        lambda model_key: succeeded_models.append(model_key),
    )

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "你好"}]}],
        tools=[{"name": "read", "description": "", "parameters": {"type": "object"}}],
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert chunks == ["来自备用模型的回复"]
    assert failed_models == ["proxy/gpt-5.4"]
    assert succeeded_models == ["proxy/bailian/qwen3.5-flash"]
    assert primary_client.chat.completions.calls[0]["model"] == "gpt-5.4"
    assert backup_client.chat.completions.calls[0]["model"] == "bailian/qwen3.5-flash"


def test_ai_service_image_request_prefers_primary_pool_when_primary_supports_image(
    monkeypatch,
):
    service = AiService()
    calls: list[tuple[str, str]] = []

    def _fake_candidates(input_type, pool_type="primary", **_kwargs):
        if input_type == "image" and pool_type == "primary":
            return ["proxy/gpt-5-codex"]
        if input_type == "image" and pool_type == "vision":
            return ["proxy/gpt-4.1"]
        return []

    def _fake_model_for_input(input_type, pool_type="primary"):
        calls.append((input_type, pool_type))
        if input_type == "image" and pool_type == "primary":
            return "proxy/gpt-5-codex"
        if input_type == "image" and pool_type == "vision":
            return "proxy/gpt-4.1"
        return ""

    monkeypatch.setattr(
        ai_service_module,
        "get_configured_model",
        lambda role: "proxy/gpt-5-codex" if role == "primary" else "",
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_candidates_for_input",
        _fake_candidates,
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_for_input",
        _fake_model_for_input,
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_id_for_api",
        lambda model_key=None: str(model_key or "").split("/", 1)[-1],
    )

    model, input_type, pool_type = service._get_model_for_request(
        [
            {
                "role": "user",
                "parts": [
                    {"text": "这张图有什么问题"},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": "ZmFrZQ==",
                        }
                    },
                ],
            }
        ]
    )

    assert (model, input_type, pool_type) == ("proxy/gpt-5-codex", "image", "primary")
    assert calls == [("image", "primary")]


def test_ai_service_image_request_uses_vision_when_primary_is_text_only(monkeypatch):
    service = AiService()
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        ai_service_module,
        "get_configured_model",
        lambda role: "proxy/gpt-5.4" if role == "primary" else "",
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_candidates_for_input",
        lambda input_type, pool_type="primary", **_kwargs: (
            []
            if input_type == "image" and pool_type == "primary"
            else ["proxy/gpt-4.1"]
        ),
    )

    def _fake_model_for_input(input_type, pool_type="primary"):
        calls.append((input_type, pool_type))
        return (
            "proxy/gpt-4.1" if input_type == "image" and pool_type == "vision" else ""
        )

    monkeypatch.setattr(
        ai_service_module,
        "get_model_for_input",
        _fake_model_for_input,
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_id_for_api",
        lambda model_key=None: str(model_key or "").split("/", 1)[-1],
    )

    model, input_type, pool_type = service._get_model_for_request(
        [
            {
                "role": "user",
                "parts": [
                    {"text": "识别这张图片"},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": "ZmFrZQ==",
                        }
                    },
                ],
            }
        ]
    )

    assert (model, input_type, pool_type) == ("proxy/gpt-4.1", "image", "vision")
    assert calls == [("image", "vision")]


@pytest.mark.asyncio
async def test_ai_service_image_request_raises_clear_error_without_image_model(
    monkeypatch,
):
    service = AiService()

    monkeypatch.setattr(ai_service_module, "openai_async_client", None)
    monkeypatch.setattr(ai_service_module, "_resolve_async_client", lambda _model: None)
    monkeypatch.setattr(
        ai_service_module,
        "get_model_for_input",
        lambda input_type, pool_type="primary": ""
        if input_type == "image"
        else "proxy/gpt-5.4",
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_candidates_for_input",
        lambda *args, **kwargs: [],
    )

    with pytest.raises(RuntimeError, match="当前没有可用的图片识别模型"):
        async for _ in service.generate_response_stream(
            message_history=[
                {
                    "role": "user",
                    "parts": [
                        {"text": "识别这张图片"},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": "ZmFrZQ==",
                            }
                        },
                    ],
                }
            ],
            system_instruction="test",
        ):
            pass
