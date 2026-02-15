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
        lambda skill_name, script_name="execute.py": SimpleNamespace(execute=fake_execute),
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
        lambda skill_name, script_name="execute.py": SimpleNamespace(execute=slow_execute),
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
