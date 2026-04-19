from types import SimpleNamespace
import json

import pytest

import services.ai_service as ai_service_module
from core.llm_usage_store import set_current_llm_usage_session_id
from core.subagent_supervisor import SubagentSupervisor
from core.subagent_types import SubagentResult
from extension.skills._internal.gh_cli_service import GhCliService
from services.ai_service import AiService


def test_ai_service_extract_response_text_handles_object_content_parts():
    response = SimpleNamespace(
        text=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=SimpleNamespace(value="来自 gpt-5.4 的正文"),
                        )
                    ],
                    tool_calls=[],
                    refusal=None,
                ),
            )
        ],
    )

    assert AiService._extract_response_text(response) == "来自 gpt-5.4 的正文"


def test_ai_service_extract_stream_text_handles_object_delta_parts():
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="text",
                            text=SimpleNamespace(value="流式片段"),
                        )
                    ]
                )
            )
        ]
    )

    assert AiService._extract_stream_text(chunk) == "流式片段"


@pytest.mark.asyncio
async def test_ai_service_upstream_requests_force_stream_and_session_id(monkeypatch):
    service = AiService()
    captured_kwargs = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="ok", tool_calls=[]),
                    )
                ]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )
    monkeypatch.setattr(ai_service_module, "openai_async_client", fake_client)
    set_current_llm_usage_session_id("chat-session")

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "hello"}]}],
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert chunks == ["ok"]
    assert captured_kwargs["stream"] is True
    assert captured_kwargs["user"] == "chat-session"
    assert captured_kwargs["extra_body"]["session_id"] == "chat-session"


@pytest.mark.asyncio
async def test_ai_service_emits_loop_guard_on_repeated_calls(monkeypatch):
    service = AiService()
    events = []
    captured_payloads = []

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
        if event == "loop_guard":
            captured_payloads.append(dict(payload))

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
    assert captured_payloads
    assert captured_payloads[-1]["repeated_calls"] == [
        {"turn": 1, "calls": [{"name": "read", "args": {"path": "a.txt"}}]},
        {"turn": 2, "calls": [{"name": "read", "args": {"path": "a.txt"}}]},
    ]
    assert "第1次（回合 1）" in captured_payloads[-1]["repeat_details"]
    assert chunks
    assert "重复工具调用" in chunks[-1]
    assert "`read` 参数 {\"path\": \"a.txt\"}" in chunks[-1]


@pytest.mark.asyncio
async def test_ai_service_loop_guard_does_not_emit_previous_terminal_success(
    monkeypatch,
):
    service = AiService()
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "2")
    monkeypatch.setenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "99")
    monkeypatch.setenv("AI_TOOL_MAX_CALLS_PER_TOOL", "99")

    class FakeResponse:
        def __init__(self):
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="bash",
                                    arguments=json.dumps(
                                        {
                                            "command": "python execute.py --stage search"
                                        },
                                        ensure_ascii=False,
                                    ),
                                ),
                            )
                        ],
                    )
                )
            ]

    async def fake_create(**_kwargs):
        return FakeResponse()

    monkeypatch.setattr(
        ai_service_module,
        "openai_async_client",
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        ),
    )

    async def fake_tool_executor(_name, _args):
        return {
            "ok": True,
            "terminal": True,
            "task_outcome": "done",
            "text": (
                "✅ search 完成\n"
                "输出: /home/luwei/ikaros/data/user/skills/article_publisher/articles/"
                "2026-04-09/research.json"
            ),
        }

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "继续完成任务"}]}],
        tools=[
            {
                "name": "bash",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=lambda _event, _payload: None,
    ):
        chunks.append(chunk)

    assert chunks
    assert "重复工具调用" in chunks[-1]
    assert "research.json" not in chunks[-1]


@pytest.mark.asyncio
async def test_ai_service_fails_over_when_first_model_returns_empty_completion(
    monkeypatch,
):
    service = AiService()
    failed_models = []
    successful_models = []

    class EmptyResponseClient:
        class _Chat:
            class _Completions:
                async def create(self, **kwargs):
                    return SimpleNamespace(
                        text=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                message=SimpleNamespace(
                                    content=None,
                                    tool_calls=[],
                                    refusal=None,
                                ),
                            )
                        ],
                    )

            def __init__(self):
                self.completions = self._Completions()

        def __init__(self):
            self.chat = self._Chat()

    class TextResponseClient:
        class _Chat:
            class _Completions:
                async def create(self, **kwargs):
                    return SimpleNamespace(
                        text=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                message=SimpleNamespace(
                                    content="fallback ok",
                                    tool_calls=[],
                                    refusal=None,
                                ),
                            )
                        ],
                    )

            def __init__(self):
                self.completions = self._Completions()

        def __init__(self):
            self.chat = self._Chat()

    clients = {
        "proxy/gpt-5.4": EmptyResponseClient(),
        "huoshan/fallback": TextResponseClient(),
    }

    monkeypatch.setattr(
        ai_service_module,
        "_resolve_async_client",
        lambda model_name: clients[model_name],
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_for_input",
        lambda input_type, pool_type="primary": "proxy/gpt-5.4",
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_id_for_api",
        lambda model_key: str(model_key or "").split("/", 1)[-1],
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_model_candidates_for_input",
        lambda input_type, pool_type="primary", preferred_model=None: [
            "proxy/gpt-5.4",
            "huoshan/fallback",
        ],
    )
    monkeypatch.setattr(
        ai_service_module,
        "mark_model_failed",
        lambda model_key: failed_models.append(model_key),
    )
    monkeypatch.setattr(
        ai_service_module,
        "mark_model_success",
        lambda model_key: successful_models.append(model_key),
    )

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "test"}]}],
        tools=[{"name": "read", "description": "", "parameters": {"type": "object"}}],
        tool_executor=lambda _name, _args: None,
        system_instruction="test",
    ):
        chunks.append(chunk)

    assert failed_models == ["proxy/gpt-5.4"]
    assert successful_models == ["huoshan/fallback"]
    assert chunks == ["fallback ok"]


@pytest.mark.asyncio
async def test_ai_service_default_tool_turn_limit_is_40(monkeypatch):
    service = AiService()
    monkeypatch.delenv("AI_TOOL_MAX_TURNS", raising=False)
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "999")
    monkeypatch.setenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "999")
    monkeypatch.setenv("AI_TOOL_MAX_CALLS_PER_TOOL", "999")

    captured = []

    async def event_callback(event, payload):
        if event == "max_turn_limit":
            captured.append(dict(payload))
        return None

    class LoopingModels:
        async def create(self, **kwargs):
            return SimpleNamespace(
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
                                            {"path": "a.txt"}, ensure_ascii=False
                                        ),
                                    ),
                                )
                            ],
                        )
                    )
                ]
            )

    monkeypatch.setattr(
        ai_service_module,
        "openai_async_client",
        SimpleNamespace(chat=SimpleNamespace(completions=LoopingModels())),
    )

    captured = []

    async def fake_tool_executor(_name, _args):
        return {"ok": True}

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "loop"}]}],
        tools=[{"name": "read", "description": "", "parameters": {"type": "object"}}],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert captured
    assert captured[-1]["max_turns"] == 40
    assert f"工具调用轮次已达上限（40）" in chunks[-1]


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
                name="spawn_subagent",
                args={
                    "goal": "检查 RSS",
                    "allowed_tools": ["read", "bash"],
                    "mode": "inline",
                },
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
                "name": "spawn_subagent",
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
async def test_ai_service_terminal_payload_includes_top_level_files(
    monkeypatch, tmp_path
):
    service = AiService()
    captured = {"payload": {}}
    image_path = (tmp_path / "demo.png").resolve()
    image_path.write_bytes(b"png")
    files = [
        {
            "kind": "photo",
            "path": str(image_path),
            "filename": "demo.png",
        }
    ]

    class FakeResponse:
        def __init__(self):
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="ext_generate_image",
                                    arguments=json.dumps(
                                        {"prompt": "生成封面"},
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
            "text": "✅ 图片已生成。",
            "files": files,
        }

    async def event_callback(event, payload):
        if event == "tool_call_finished":
            captured["payload"] = dict(payload.get("terminal_payload") or {})
            return {"stop": True, "final_text": payload.get("terminal_text", "")}
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "画图"}]}],
        tools=[
            {
                "name": "ext_generate_image",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert chunks[-1] == "✅ 图片已生成。"
    assert captured["payload"]["files"] == [{**files[0], "caption": ""}]


@pytest.mark.asyncio
async def test_ai_service_continues_after_await_subagents_collection(monkeypatch):
    service = AiService()
    supervisor = SubagentSupervisor()
    supervisor._runs["subagent-1"] = SimpleNamespace(
        subagent_id="subagent-1",
        task=None,
        result=SubagentResult(
            subagent_id="subagent-1",
            ok=False,
            summary="未能加载 article_publisher",
            text="未能加载 article_publisher",
            error="未能加载 article_publisher",
            diagnostic_summary="未能加载 article_publisher",
            task_outcome="blocked",
            failure_mode="recoverable",
            ikaros_followup_required=True,
        ),
    )

    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name="await_subagents",
                                            arguments=json.dumps(
                                                {
                                                    "subagent_ids": ["subagent-1"],
                                                    "wait_policy": "all",
                                                },
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
                            content="子任务执行失败：未能加载 article_publisher，因此没有生成公众号草稿。",
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

    async def fake_tool_executor(name, args):
        assert name == "await_subagents"
        return await supervisor.await_subagents(
            subagent_ids=args.get("subagent_ids") or [],
            wait_policy=str(args.get("wait_policy") or "all"),
        )

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "继续"}]}],
        tools=[
            {
                "name": "await_subagents",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=None,
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "子任务执行失败：未能加载 article_publisher，因此没有生成公众号草稿。"


@pytest.mark.asyncio
async def test_ai_service_continues_after_terminal_tool_requests_retry(monkeypatch):
    service = AiService()

    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name="bash",
                                            arguments=json.dumps(
                                                {
                                                    "command": "python execute.py --stage search"
                                                },
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
                            content="已完成文章撰写并发布到公众号草稿箱。",
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

    async def fake_tool_executor(_name, _args):
        return {
            "ok": True,
            "terminal": True,
            "task_outcome": "done",
            "text": (
                "✅ search 完成\n"
                "输出:\n"
                "/home/luwei/ikaros/data/user/skills/article_publisher/articles/"
                "2026-04-09/research.json"
            ),
        }

    async def event_callback(event, payload):
        if event == "tool_call_finished":
            return {
                "continue_prompt": (
                    "系统提示：上一步结果只是中间产物，请继续完成整篇文章并给出最终交付。"
                )
            }
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "写文章并发布"}]}],
        tools=[
            {
                "name": "bash",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "已完成文章撰写并发布到公众号草稿箱。"


@pytest.mark.asyncio
async def test_ai_service_continues_after_final_response_requests_retry(monkeypatch):
    service = AiService()

    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=(
                                    "✅ search 完成\n"
                                    "输出: /home/luwei/ikaros/data/user/skills/"
                                    "article_publisher/articles/2026-04-09/research.json"
                                ),
                                tool_calls=[],
                            )
                        )
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="已整理好最终稿，并说明了公众号草稿已创建。",
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

    seen = {"count": 0}

    async def event_callback(event, payload):
        if event == "final_response":
            seen["count"] += 1
            text = str(payload.get("text") or "")
            if "research.json" in text:
                return {
                    "continue_prompt": (
                        "系统提示：这只是检索阶段结果，请继续产出面向用户的最终交付。"
                    )
                }
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "继续完成任务"}]}],
        tools=[
            {
                "name": "bash",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=lambda _name, _args: None,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert seen["count"] == 2
    assert "".join(chunks) == "已整理好最终稿，并说明了公众号草稿已创建。"


@pytest.mark.asyncio
async def test_ai_service_max_turn_limit_does_not_emit_intermediate_terminal_text(
    monkeypatch,
):
    service = AiService()
    monkeypatch.setenv("AI_TOOL_MAX_TURNS", "2")
    monkeypatch.setenv("AI_TOOL_REPEAT_GUARD", "99")
    monkeypatch.setenv("AI_TOOL_SEMANTIC_REPEAT_GUARD", "99")
    monkeypatch.setenv("AI_TOOL_MAX_CALLS_PER_TOOL", "99")

    class LoopingModels:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[
                                SimpleNamespace(
                                    id="call-1",
                                    function=SimpleNamespace(
                                        name="bash",
                                        arguments=json.dumps(
                                            {
                                                "command": "python execute.py --stage search"
                                            },
                                            ensure_ascii=False,
                                        ),
                                    ),
                                )
                            ],
                        )
                    )
                ]
            )

    monkeypatch.setattr(
        ai_service_module,
        "openai_async_client",
        SimpleNamespace(chat=SimpleNamespace(completions=LoopingModels())),
    )

    async def fake_tool_executor(_name, _args):
        return {
            "ok": True,
            "terminal": True,
            "task_outcome": "done",
            "text": (
                "✅ search 完成\n"
                "输出:\n"
                "/home/luwei/ikaros/data/user/skills/article_publisher/articles/"
                "2026-04-09/research.json"
            ),
        }

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "继续写完文章"}]}],
        tools=[
            {
                "name": "bash",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=None,
    ):
        chunks.append(chunk)

    assert chunks
    assert "工具调用轮次已达上限（2）" in chunks[-1]
    assert "research.json" not in chunks[-1]


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
                                            name="spawn_subagent",
                                            arguments=json.dumps(
                                                {
                                                    "goal": "查询明天天气",
                                                    "allowed_tools": ["web_search"],
                                                    "mode": "detached",
                                                },
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
                                "已启动 subagent-weather（任务 wj-1），"
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
            "executor_name": "subagent-weather",
            "task_id": "wj-1",
            "task_outcome": "partial",
            "text": "subagent started",
            "summary": "subagent job queued",
        }

    async def event_callback(event, payload):
        events.append((event, payload))

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "查天气"}]}],
        tools=[
            {
                "name": "spawn_subagent",
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


@pytest.mark.asyncio
async def test_ai_service_does_not_short_circuit_on_gh_auth_probe_success(
    monkeypatch, tmp_path
):
    service = AiService()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name="gh_cli",
                                            arguments=json.dumps(
                                                {
                                                    "action": "auth_status",
                                                    "hostname": "github.com",
                                                },
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
                            content="继续执行了后续开发步骤。",
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

    gh_service = GhCliService()

    async def fake_status(hostname: str):
        assert hostname == "github.com"
        return {
            "authenticated": True,
            "text": "Logged in to github.com as octocat",
            "raw": {"ok": True},
        }

    monkeypatch.setattr(gh_service, "_auth_status_command", fake_status)

    async def fake_tool_executor(name, args):
        assert name == "gh_cli"
        return await gh_service.handle(**dict(args))

    events = []

    async def event_callback(event, payload):
        events.append((event, dict(payload)))
        if event == "tool_call_finished" and payload.get("terminal"):
            return {
                "stop": True,
                "final_text": str(payload.get("terminal_text") or ""),
            }
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "继续开发"}]}],
        tools=[
            {
                "name": "gh_cli",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert chunks == ["继续执行了后续开发步骤。"]
    assert any(
        name == "tool_call_finished"
        and payload.get("name") == "gh_cli"
        and payload.get("terminal") is False
        for name, payload in events
    )


@pytest.mark.asyncio
async def test_ai_service_does_not_short_circuit_on_gh_exec_success(
    monkeypatch, tmp_path
):
    service = AiService()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name="gh_cli",
                                            arguments=json.dumps(
                                                {
                                                    "action": "exec",
                                                    "hostname": "github.com",
                                                    "argv": [
                                                        "pr",
                                                        "list",
                                                        "--repo",
                                                        "Scenx/fuck-skill",
                                                        "--json",
                                                        "number",
                                                    ],
                                                    "timeout_sec": 30,
                                                },
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
                            content="后续步骤还会继续，不会把 [] 直接回给用户。",
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

    gh_service = GhCliService()

    async def fake_run_capture(argv, *, cwd=None, timeout_sec=120):
        assert argv == [
            "gh",
            "pr",
            "list",
            "--repo",
            "Scenx/fuck-skill",
            "--json",
            "number",
        ]
        assert cwd is None
        assert timeout_sec == 30
        return {
            "ok": True,
            "exit_code": 0,
            "output": "[]",
            "stdout": "[]",
            "stderr": "",
        }

    monkeypatch.setattr(gh_service, "_run_capture", fake_run_capture)

    async def fake_tool_executor(name, args):
        assert name == "gh_cli"
        return await gh_service.handle(**dict(args))

    events = []

    async def event_callback(event, payload):
        events.append((event, dict(payload)))
        if event == "tool_call_finished" and payload.get("terminal"):
            return {
                "stop": True,
                "final_text": str(payload.get("terminal_text") or ""),
            }
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "继续开发"}]}],
        tools=[
            {
                "name": "gh_cli",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert chunks == ["后续步骤还会继续，不会把 [] 直接回给用户。"]
    assert any(
        name == "tool_call_finished"
        and payload.get("name") == "gh_cli"
        and payload.get("terminal") is False
        and payload.get("summary") == "[]"
        for name, payload in events
    )


@pytest.mark.asyncio
async def test_ai_service_continues_after_send_local_file_success(monkeypatch):
    service = AiService()

    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(
                                            name="send_local_file",
                                            arguments=json.dumps(
                                                {
                                                    "path": "/tmp/baby_camera_latest.jpg",
                                                    "caption": "请查收",
                                                },
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
                            content="宝宝在床上，正在安静睡觉。",
                            tool_calls=[],
                        )
                    )
                ]
            )

    monkeypatch.setattr(
        ai_service_module,
        "openai_async_client",
        SimpleNamespace(chat=SimpleNamespace(completions=FakeModels())),
    )

    async def fake_tool_executor(name, args):
        assert name == "send_local_file"
        assert args["path"] == "/tmp/baby_camera_latest.jpg"
        return {
            "ok": True,
            "terminal": False,
            "summary": "Sent local file baby_camera_latest.jpg",
            "text": "📎 已发送文件：baby_camera_latest.jpg",
            "payload": {
                "text": "📎 已发送文件：baby_camera_latest.jpg",
                "files": [
                    {
                        "path": "/tmp/baby_camera_latest.jpg",
                        "filename": "baby_camera_latest.jpg",
                        "kind": "photo",
                    }
                ],
            },
        }

    events = []

    async def event_callback(event, payload):
        events.append((event, dict(payload)))
        if event == "tool_call_finished" and payload.get("terminal"):
            return {
                "stop": True,
                "final_text": str(payload.get("terminal_text") or ""),
            }
        return None

    chunks = []
    async for chunk in service.generate_response_stream(
        message_history=[{"role": "user", "parts": [{"text": "把图发给我并告诉我宝宝状态"}]}],
        tools=[
            {
                "name": "send_local_file",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        tool_executor=fake_tool_executor,
        system_instruction="test",
        event_callback=event_callback,
    ):
        chunks.append(chunk)

    assert chunks == ["宝宝在床上，正在安静睡觉。"]
    assert any(
        name == "tool_call_finished"
        and payload.get("name") == "send_local_file"
        and payload.get("terminal") is False
        for name, payload in events
    )
