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
                "handler": "ikaros.queue.status",
                "allowed_roles": ["ikaros"],
            },
            {
                "name": "subagent_only_demo",
                "description": "Subagent-only tool",
                "parameters": {"type": "object", "properties": {}},
                "handler": "subagent.demo",
                "allowed_roles": ["subagent"],
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
                "handler": "ikaros.queue.status",
                "allowed_roles": ["ikaros"],
            }
        }.get(name),
    )

    registry = ToolRegistry()

    ikaros_tools = registry.get_skill_tools(runtime_role="ikaros")
    subagent_tools = registry.get_skill_tools(runtime_role="subagent")
    binding = registry.get_skill_tool_binding("queue_status", runtime_role="ikaros")

    assert [item["name"] for item in ikaros_tools] == ["queue_status"]
    assert [item["name"] for item in subagent_tools] == ["subagent_only_demo"]
    assert binding["handler"] == "ikaros.queue.status"


@pytest.mark.asyncio
async def test_runtime_tool_assembler_injects_ikaros_skill_tools():
    assembler = RuntimeToolAssembler(
        runtime_user_id="u-1",
        platform_name="telegram",
        runtime_tool_allowed=lambda **_kwargs: True,
    )

    tools = await assembler.assemble()
    names = [tool["name"] for tool in tools]

    assert {"read", "write", "edit", "bash", "load_skill"} <= set(names)
    assert {
        "await_subagents",
        "codex_session",
        "git_ops",
        "gh_cli",
        "repo_workspace",
        "send_local_file",
        "spawn_subagent",
        "task_tracker",
    } <= set(names)
    assert "analyze_video" not in names


@pytest.mark.asyncio
async def test_runtime_tool_assembler_keeps_subagent_surface_without_ikaros_control_plane_tools():
    assembler = RuntimeToolAssembler(
        runtime_user_id="subagent::subagent-main::u-1",
        platform_name="subagent_kernel",
        runtime_tool_allowed=lambda **_kwargs: True,
        allowed_tool_names={"read", "write", "edit", "bash", "load_skill"},
    )

    tools = await assembler.assemble()
    names = [tool["name"] for tool in tools]

    assert names == ["read", "write", "edit", "bash", "load_skill"]


@pytest.mark.asyncio
async def test_dispatcher_accepts_ikaros_runtime_only_tools_after_skill_load(
    monkeypatch,
):
    captured = {}

    async def fake_codex_session(**kwargs):
        captured.update(dict(kwargs))
        return {"ok": True, "summary": "ok"}

    monkeypatch.setattr(
        "core.skill_tool_handlers.codex_tools.codex_session",
        fake_codex_session,
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
            "await_subagents",
            "spawn_subagent",
            "repo_workspace",
            "codex_session",
            "git_ops",
            "gh_cli",
        },
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"read", "write", "edit", "bash", "load_skill"})

    result = await dispatcher.execute(
        name="codex_session",
        args={"action": "status", "session_id": "cx-1"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["action"] == "status"
    assert captured["session_id"] == "cx-1"


@pytest.mark.asyncio
async def test_dispatcher_blocks_subagent_control_plane_for_subagent_runtime():
    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="subagent::subagent-main::u-1",
        platform_name="subagent_kernel",
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
        name="spawn_subagent",
        args={"goal": "parallelize", "allowed_tools": ["bash"]},
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

    skill_dir = "/tmp/extension/skills/daily_query"
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
    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "get_enabled_skill",
        lambda _skill_name: {
            "name": "daily_query",
            "skill_md_content": "# Daily Query",
            "skill_dir": skill_dir,
            "entrypoint": "scripts/execute.py",
        },
    )
    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "is_skill_enabled",
        lambda _skill_name: True,
    )

    dispatcher = ToolCallDispatcher(
        runtime_user_id="subagent::subagent-main::u1",
        platform_name="subagent_kernel",
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
async def test_load_skill_blocks_ikaros_only_skill_for_subagent(monkeypatch):
    async def append_event(_event: str):
        return None

    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "get_skill",
        lambda _skill_name: {
            "name": "skill_manager",
            "allowed_roles": ["ikaros"],
            "contract": {"runtime_target": "ikaros"},
            "skill_md_content": "# Skill Ikaros",
            "skill_dir": "/tmp/extension/skills/skill_manager",
            "entrypoint": "scripts/execute.py",
        },
    )
    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "get_enabled_skill",
        lambda _skill_name: {
            "name": "skill_manager",
            "allowed_roles": ["ikaros"],
            "contract": {"runtime_target": "ikaros"},
            "skill_md_content": "# Skill Ikaros",
            "skill_dir": "/tmp/extension/skills/skill_manager",
            "entrypoint": "scripts/execute.py",
        },
    )
    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "is_skill_enabled",
        lambda _skill_name: True,
    )

    dispatcher = ToolCallDispatcher(
        runtime_user_id="subagent::subagent-main::u1",
        platform_name="subagent_kernel",
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
            (
                False,
                {"reason": "matched_deny_list"},
            )
            if kwargs.get("tool_name") == "ext_skill_manager"
            else True
        ),
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
async def test_load_skill_blocks_disabled_skill(monkeypatch):
    async def append_event(_event: str):
        return None

    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "get_skill",
        lambda _skill_name: {
            "name": "news_article_writer",
            "skill_md_content": "# Disabled",
            "skill_dir": "/tmp/extension/skills/news_article_writer",
            "entrypoint": "scripts/execute.py",
        },
    )
    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "is_skill_enabled",
        lambda _skill_name: False,
    )
    monkeypatch.setattr(
        runtime_tools_module.skill_loader,
        "get_enabled_skill",
        lambda _skill_name: None,
    )

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-1",
        platform_name="telegram",
        task_id="task-disabled",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="加载被禁用技能",
                user=SimpleNamespace(id="source-user-1"),
                chat=SimpleNamespace(id="chat-99"),
            ),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"load_skill"})

    result = await dispatcher.execute(
        name="load_skill",
        args={"skill_name": "news_article_writer"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is False
    assert result["error_code"] == "skill_disabled"


@pytest.mark.asyncio
async def test_bash_env_prefers_forced_subagent_delivery_target(monkeypatch):
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
                text="启动子任务",
                user=SimpleNamespace(id="user-origin-7"),
                chat=SimpleNamespace(id="chat-origin-7"),
            ),
            user_data={
                "subagent_delivery_platform": "discord",
                "subagent_delivery_chat_id": "discord-target-8",
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
            "command": "cd skills/builtin/deployment_manager && python scripts/execute.py help"
        },
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["name"] == "bash"
    assert captured["args"]["command"].startswith("export ")
    assert "X_BOT_RUNTIME_CHAT_ID=chat-chain-1" in captured["args"]["command"]
    assert (
        "&& cd skills/builtin/deployment_manager && python scripts/execute.py help"
        in captured["args"]["command"]
    )
