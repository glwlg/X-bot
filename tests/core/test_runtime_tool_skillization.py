from types import SimpleNamespace
import time

import pytest

from core.orchestrator_runtime_tools import RuntimeToolAssembler, ToolCallDispatcher
import core.orchestrator_runtime_tools as runtime_tools_module
from core.tool_registry import ToolRegistry


def test_tool_registry_builds_skill_tools_from_loader_metadata(monkeypatch):
    monkeypatch.setattr(
        "core.tool_registry.skill_loader.get_tool_exports",
        lambda: [
            {
                "name": "queue_status",
                "description": "Query queue status",
                "parameters": {"type": "object", "properties": {}},
                "handler": "manager.queue.status",
                "allowed_roles": ["manager"],
            },
            {
                "name": "worker_only_demo",
                "description": "Worker-only tool",
                "parameters": {"type": "object", "properties": {}},
                "handler": "worker.demo",
                "allowed_roles": ["worker"],
            },
        ],
    )
    monkeypatch.setattr(
        "core.tool_registry.skill_loader.get_tool_export",
        lambda name: {
            "queue_status": {
                "name": "queue_status",
                "description": "Query queue status",
                "parameters": {"type": "object", "properties": {}},
                "handler": "manager.queue.status",
                "allowed_roles": ["manager"],
            }
        }.get(name),
    )

    registry = ToolRegistry()

    manager_tools = registry.get_skill_tools(runtime_role="manager")
    worker_tools = registry.get_skill_tools(runtime_role="worker")
    binding = registry.get_skill_tool_binding("queue_status", runtime_role="manager")

    assert [item["name"] for item in manager_tools] == ["queue_status"]
    assert [item["name"] for item in worker_tools] == ["worker_only_demo"]
    assert binding["handler"] == "manager.queue.status"


@pytest.mark.asyncio
async def test_runtime_tool_assembler_injects_manager_skill_tools():
    assembler = RuntimeToolAssembler(
        runtime_user_id="u-1",
        platform_name="telegram",
        runtime_tool_allowed=lambda **_kwargs: True,
    )

    tools = await assembler.assemble()
    names = [tool["name"] for tool in tools]

    assert names[:5] == ["read", "write", "edit", "bash", "load_skill"]
    assert set(names[5:]) == {
        "dispatch_worker",
        "list_workers",
        "software_delivery",
        "worker_status",
    }


@pytest.mark.asyncio
async def test_runtime_tool_assembler_keeps_worker_surface_without_software_delivery():
    assembler = RuntimeToolAssembler(
        runtime_user_id="worker::worker-main::u-1",
        platform_name="worker_kernel",
        runtime_tool_allowed=lambda **_kwargs: True,
    )

    tools = await assembler.assemble()
    names = [tool["name"] for tool in tools]

    assert names == ["read", "write", "edit", "bash", "load_skill"]


@pytest.mark.asyncio
async def test_dispatcher_accepts_manager_runtime_only_tools_after_skill_load(
    monkeypatch,
):
    captured = {}

    async def fake_software_delivery(**kwargs):
        captured.update(dict(kwargs))
        return {"ok": True, "summary": "ok"}

    monkeypatch.setattr(
        "core.skill_tool_handlers.dev_tools.software_delivery",
        fake_software_delivery,
    )

    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-1",
        platform_name="telegram",
        task_id="task-1",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="查看开发任务状态"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **kwargs: kwargs.get("tool_name")
        in {
            "read",
            "write",
            "edit",
            "bash",
            "load_skill",
            "list_workers",
            "dispatch_worker",
            "worker_status",
            "software_delivery",
        },
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"read", "write", "edit", "bash", "load_skill"})

    result = await dispatcher.execute(
        name="software_delivery",
        args={"action": "status", "task_id": "dev-1"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["action"] == "status"
    assert captured["task_id"] == "dev-1"


@pytest.mark.asyncio
async def test_dispatcher_keeps_runtime_only_management_tools_blocked_for_worker():
    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="worker::worker-main::u-1",
        platform_name="worker_kernel",
        task_id="task-2",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="status"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **kwargs: kwargs.get("tool_name")
        in {"read", "write", "edit", "bash", "load_skill"},
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"read", "write", "edit", "bash", "load_skill"})

    result = await dispatcher.execute(
        name="software_delivery",
        args={"action": "status"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is False
    assert result["error_code"] == "unknown_tool"


@pytest.mark.asyncio
async def test_load_skill_sets_bash_cwd_for_relative_entrypoint(monkeypatch):
    captured = {}

    class _FakeToolBroker:
        async def execute_core_tool(
            self,
            *,
            name,
            args,
            execution_policy,
            task_workspace_root,
        ):
            _ = (execution_policy, task_workspace_root)
            captured["name"] = name
            captured["args"] = dict(args or {})
            return {"ok": True, "summary": "ok"}

    async def append_event(_event: str):
        return None

    skill_dir = "/tmp/skills/daily_query"
    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "get_skill",
        lambda _skill_name: {
            "name": "daily_query",
            "skill_md_content": "# Daily Query",
            "skill_dir": skill_dir,
            "entrypoint": "scripts/execute.py",
        },
    )

    dispatcher = ToolCallDispatcher(
        runtime_user_id="worker::worker-main::u1",
        platform_name="worker_kernel",
        task_id="task-6",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="查询无锡天气",
                user=SimpleNamespace(id="source-user-1"),
                chat=SimpleNamespace(id="chat-99"),
            ),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"load_skill", "bash"})

    load_result = await dispatcher.execute(
        name="load_skill",
        args={"skill_name": "daily_query"},
        execution_policy=None,
        started=time.perf_counter(),
    )
    assert load_result["ok"] is True
    assert load_result["skill_dir"] == skill_dir

    bash_result = await dispatcher.execute(
        name="bash",
        args={"command": "python scripts/execute.py weather 无锡"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert bash_result["ok"] is True
    assert captured["name"] == "bash"
    assert captured["args"]["cwd"] == skill_dir
    assert captured["args"]["command"].startswith("export ")


@pytest.mark.asyncio
async def test_load_skill_blocks_manager_only_skill_for_worker(monkeypatch):
    async def append_event(_event: str):
        return None

    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "get_skill",
        lambda _skill_name: {
            "name": "skill_manager",
            "allowed_roles": ["manager"],
            "contract": {"runtime_target": "manager"},
            "skill_md_content": "# Skill Manager",
            "skill_dir": "/tmp/skills/skill_manager",
            "entrypoint": "scripts/execute.py",
        },
    )

    dispatcher = ToolCallDispatcher(
        runtime_user_id="worker::worker-main::u1",
        platform_name="worker_kernel",
        task_id="task-7",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="管理技能",
                user=SimpleNamespace(id="source-user-1"),
                chat=SimpleNamespace(id="chat-99"),
            ),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **kwargs: (
            False,
            {"reason": "matched_deny_list"},
        )
        if kwargs.get("tool_name") == "ext_skill_manager"
        else True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"load_skill"})

    result = await dispatcher.execute(
        name="load_skill",
        args={"skill_name": "skill_manager"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is False
    assert result["error_code"] == "skill_policy_blocked"


@pytest.mark.asyncio
async def test_bash_env_prefers_forced_worker_delivery_target(monkeypatch):
    captured = {}

    class _FakeToolBroker:
        async def execute_core_tool(
            self,
            *,
            name,
            args,
            execution_policy,
            task_workspace_root,
        ):
            _ = (execution_policy, task_workspace_root)
            captured["name"] = name
            captured["args"] = dict(args or {})
            return {"ok": True, "summary": "ok"}

    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-telegram-1",
        platform_name="telegram",
        task_id="task-7",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="派发 worker 任务",
                user=SimpleNamespace(id="user-origin-7"),
                chat=SimpleNamespace(id="chat-origin-7"),
            ),
            user_data={
                "worker_delivery_platform": "discord",
                "worker_delivery_chat_id": "discord-target-8",
            },
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"bash"})

    result = await dispatcher.execute(
        name="bash",
        args={"command": "python scripts/execute.py dispatch do-something"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["name"] == "bash"
    assert captured["args"]["command"].startswith("export ")
    assert "X_BOT_RUNTIME_USER_ID=u-telegram-1" in captured["args"]["command"]
    assert "X_BOT_RUNTIME_SOURCE_USER_ID=user-origin-7" in captured["args"]["command"]
    assert "X_BOT_RUNTIME_PLATFORM=discord" in captured["args"]["command"]
    assert "X_BOT_RUNTIME_CHAT_ID=discord-target-8" in captured["args"]["command"]
    assert captured["args"]["command"].endswith(
        "python scripts/execute.py dispatch do-something"
    )


@pytest.mark.asyncio
async def test_bash_env_export_wraps_chained_commands(monkeypatch):
    captured = {}

    class _FakeToolBroker:
        async def execute_core_tool(
            self,
            *,
            name,
            args,
            execution_policy,
            task_workspace_root,
        ):
            _ = (execution_policy, task_workspace_root)
            captured["name"] = name
            captured["args"] = dict(args or {})
            return {"ok": True, "summary": "ok"}

    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-chain-1",
        platform_name="telegram",
        task_id="task-8",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="链式 bash 测试",
                user=SimpleNamespace(id="user-chain-1"),
                chat=SimpleNamespace(id="chat-chain-1"),
            ),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"bash"})

    result = await dispatcher.execute(
        name="bash",
        args={
            "command": "cd skills/builtin/worker_management && python scripts/execute.py dispatch hi"
        },
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["name"] == "bash"
    assert captured["args"]["command"].startswith("export ")
    assert "X_BOT_RUNTIME_CHAT_ID=chat-chain-1" in captured["args"]["command"]
    assert "&& cd skills/builtin/worker_management && python scripts/execute.py dispatch hi" in captured[
        "args"
    ]["command"]
