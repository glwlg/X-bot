from types import SimpleNamespace
import time

import pytest

from core.orchestrator_runtime_tools import RuntimeToolAssembler, ToolCallDispatcher
import core.orchestrator_runtime_tools as runtime_tools_module


@pytest.mark.asyncio
async def test_runtime_tool_assembler_only_injects_primitives_and_load_skill():
    assembler = RuntimeToolAssembler(
        runtime_user_id="u-1",
        platform_name="telegram",
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
        runtime_tools_module.dev_tools,
        "software_delivery",
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
        record_tool_profile=lambda *_args, **_kwargs: None,
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
        record_tool_profile=lambda *_args, **_kwargs: None,
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
        record_tool_profile=lambda *_args, **_kwargs: None,
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
    assert "X_BOT_RUNTIME_USER_ID=u1" in captured["args"]["command"]
    assert "X_BOT_RUNTIME_SOURCE_USER_ID=source-user-1" in captured["args"]["command"]
    assert "X_BOT_RUNTIME_PLATFORM=worker_kernel" in captured["args"]["command"]
    assert "X_BOT_RUNTIME_CHAT_ID=chat-99" in captured["args"]["command"]


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
        record_tool_profile=lambda *_args, **_kwargs: None,
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
        record_tool_profile=lambda *_args, **_kwargs: None,
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
