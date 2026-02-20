from types import SimpleNamespace
import asyncio

import pytest

from core.extension_executor import ExtensionExecutor, ExtensionRunResult
import core.extension_executor as extension_executor_module


@pytest.mark.asyncio
async def test_extension_executor_runs_skill(monkeypatch):
    async def fake_execute(ctx, args, runtime):
        return {"text": f"done:{args['query']}", "success": True}

    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill",
        lambda name: {
            "name": "demo_skill",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "entrypoint": "scripts/execute.py",
        }
        if name == "demo_skill"
        else None,
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "import_skill_module",
        lambda skill_name, script_name="execute.py": SimpleNamespace(
            execute=fake_execute
        ),
    )

    executor = ExtensionExecutor()
    result = await executor.execute(
        "demo_skill",
        {"query": "abc"},
        ctx=object(),
        runtime=object(),
    )

    assert result.ok is True
    assert result.text == "done:abc"


@pytest.mark.asyncio
async def test_extension_executor_validates_required_fields(monkeypatch):
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill",
        lambda name: {
            "name": "demo_skill",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "entrypoint": "scripts/execute.py",
        }
        if name == "demo_skill"
        else None,
    )

    executor = ExtensionExecutor()
    result = await executor.execute(
        "demo_skill",
        {},
        ctx=object(),
        runtime=object(),
    )

    assert result.ok is False
    assert result.error_code == "invalid_args"
    assert "missing required field" in result.message
    assert result.missing_fields == ["query"]


@pytest.mark.asyncio
async def test_extension_executor_timeout_isolated(monkeypatch):
    async def slow_execute(ctx, args, runtime):
        await asyncio.sleep(0.2)
        return {"text": "late"}

    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill",
        lambda name: {
            "name": "slow_skill",
            "input_schema": {"type": "object", "properties": {}},
            "entrypoint": "scripts/execute.py",
        }
        if name == "slow_skill"
        else None,
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "import_skill_module",
        lambda skill_name, script_name="execute.py": SimpleNamespace(
            execute=slow_execute
        ),
    )
    monkeypatch.setattr(extension_executor_module, "EXTENSION_EXEC_TIMEOUT_SEC", 0.05)

    executor = ExtensionExecutor()
    result = await executor.execute(
        "slow_skill",
        {},
        ctx=object(),
        runtime=object(),
    )

    assert result.ok is False
    assert result.error_code == "execution_timeout"


def test_extension_failed_tool_response_preserves_terminal_flags():
    result = ExtensionRunResult(
        ok=False,
        skill_name="deployment_manager",
        text="❌ 部署失败",
        data={"terminal": True, "task_outcome": "failed"},
        error_code="skill_failed",
        message="❌ 部署失败",
    )

    payload = result.to_tool_response()
    assert payload["ok"] is False
    assert payload["terminal"] is True
    assert payload["task_outcome"] == "failed"
    assert "部署失败" in payload["summary"]


def test_extension_ui_response_defaults_to_terminal_done():
    result = ExtensionRunResult(
        ok=True,
        skill_name="rss_subscribe",
        text="这是你的订阅列表",
        data={
            "ui": {
                "actions": [
                    [
                        {"text": "刷新", "callback_data": "rss_refresh"},
                        {"text": "取消", "callback_data": "rss_cancel"},
                    ]
                ]
            }
        },
    )

    payload = result.to_tool_response()
    assert payload["ok"] is True
    assert payload["terminal"] is True
    assert payload["task_outcome"] == "done"
    assert payload["ui"]["actions"][0][0]["text"] == "刷新"


def test_extension_executor_marks_error_text_as_failure():
    executor = ExtensionExecutor()

    result = executor._normalize_result(
        "web_browser",
        {"text": "❌ 请提供 URL"},
    )

    assert result.ok is False
    assert result.error_code == "skill_failed"


@pytest.mark.parametrize(
    "warning_text",
    [
        "⚠️ 工具调用轮次已达上限（20），任务仍未完成。",
        "⚠️ 已达到单工具调用上限，停止继续重复调用。",
        "⚠️ 检测到语义上重复的工具调用，已停止继续搜索。",
    ],
)
def test_extension_executor_marks_guard_warning_text_as_failure(warning_text):
    executor = ExtensionExecutor()

    result = executor._normalize_result(
        "playwright-cli",
        {"text": warning_text},
    )

    assert result.ok is False
    assert result.error_code == "skill_failed"


@pytest.mark.asyncio
async def test_standard_skill_runs_allowed_bash_via_agent_loop(monkeypatch):
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill",
        lambda name: {
            "name": "playwright-cli",
            "input_schema": {"type": "object", "properties": {}},
            "allowed_tools": ["Bash(playwright-cli:*)"],
            "skill_md_content": "Use playwright-cli commands to operate browser.",
            "entrypoint": "",
        }
        if name == "playwright-cli"
        else None,
    )

    class FakeAiService:
        async def generate_response_stream(
            self,
            message_history,
            tools,
            tool_executor,
            system_instruction,
            event_callback=None,
        ):
            _ = (message_history, system_instruction, event_callback)
            assert tools and tools[0]["name"] == "bash"
            result = await tool_executor(
                "bash",
                {"command": "playwright-cli open https://example.com"},
            )
            assert result["ok"] is True
            yield "workflow done"

    monkeypatch.setattr(extension_executor_module, "AiService", FakeAiService)

    class FakeRuntime:
        async def bash(self, **kwargs):
            assert kwargs.get("command", "").startswith("playwright-cli")
            return {"ok": True, "summary": "ok"}

    executor = ExtensionExecutor()
    result = await executor.execute(
        "playwright-cli",
        {},
        ctx=SimpleNamespace(message=SimpleNamespace(text="打开网页")),
        runtime=FakeRuntime(),
    )

    assert result.ok is True
    assert "workflow done" in result.text
    assert result.data.get("standard_skill") is True


@pytest.mark.asyncio
async def test_standard_skill_bash_prefix_policy_blocked():
    executor = ExtensionExecutor()

    class FakeRuntime:
        async def bash(self, **kwargs):
            _ = kwargs
            return {"ok": True}

    result = await executor._execute_standard_tool_call(
        runtime=FakeRuntime(),
        tool_name="bash",
        tool_args={"command": "rm -rf /"},
        allowed_tool_rules={
            "bash": {
                "tool_name": "bash",
                "bash_prefixes": ["playwright-cli"],
            }
        },
    )

    assert result["ok"] is False
    assert result["error_code"] == "policy_blocked"
