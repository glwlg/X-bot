import time
from types import SimpleNamespace

import pytest
from core.orchestrator_runtime_tools import ToolCallDispatcher
import core.orchestrator_runtime_tools as runtime_tools_module


def test_runtime_tool_dispatcher_no_longer_uses_legacy_extension_executor():
    assert hasattr(runtime_tools_module, "skill_loader")
    assert not hasattr(runtime_tools_module, "extension_tools")
    assert not hasattr(runtime_tools_module, "skill_arg_planner")


@pytest.mark.asyncio
async def test_software_delivery_falls_back_to_user_request(monkeypatch):
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
        task_id="task-2",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="帮我创建一个邮政编码查询的技能"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"software_delivery"})

    result = await dispatcher.execute(
        name="software_delivery",
        args={"action": "skill_create", "skill_name": "postal_code_lookup_cn"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured.get("action") == "skill_create"
    assert captured.get("instruction") == "帮我创建一个邮政编码查询的技能"
    assert captured.get("requirement") == "帮我创建一个邮政编码查询的技能"


@pytest.mark.asyncio
async def test_software_delivery_inferrs_skill_action_from_request(monkeypatch):
    captured = {}

    async def fake_software_delivery(**kwargs):
        captured.update(dict(kwargs))
        return {"ok": False, "summary": "workspace is not a git repository"}

    monkeypatch.setattr(
        "core.skill_tool_handlers.dev_tools.software_delivery",
        fake_software_delivery,
    )

    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-5",
        platform_name="telegram",
        task_id="task-5",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="帮我创建一个邮政编码查询的技能"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"software_delivery"})

    await dispatcher.execute(
        name="software_delivery",
        args={},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert captured.get("action") == "skill_create"


@pytest.mark.asyncio
async def test_software_delivery_rewrites_plan_to_skill_modify_without_repo_hints(
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
        runtime_user_id="u-7",
        platform_name="telegram",
        task_id="task-7",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="请排查并修复这个技能"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"software_delivery"})

    await dispatcher.execute(
        name="software_delivery",
        args={
            "action": "plan",
            "repo_path": ".",
            "requirement": "排查并修复 ext_postal_code_query 技能",
        },
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert captured.get("action") == "skill_modify"


@pytest.mark.asyncio
async def test_software_delivery_keeps_plan_when_repo_hint_present(monkeypatch):
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
        runtime_user_id="u-8",
        platform_name="telegram",
        task_id="task-8",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="请排查并修复这个技能"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"software_delivery"})

    await dispatcher.execute(
        name="software_delivery",
        args={
            "action": "plan",
            "repo_url": "https://github.com/acme/repo.git",
            "requirement": "排查并修复 ext_postal_code_query 技能",
        },
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert captured.get("action") == "plan"


@pytest.mark.asyncio
async def test_software_delivery_external_skill_integration_routes_to_local_skill_create(
    monkeypatch,
):
    captured = {}

    async def fake_software_delivery(**kwargs):
        captured.update(dict(kwargs))
        return {"ok": True, "summary": "queued"}

    monkeypatch.setattr(
        "core.skill_tool_handlers.dev_tools.software_delivery",
        fake_software_delivery,
    )

    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-9",
        platform_name="telegram",
        task_id="task-9",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="https://github.com/runningZ1/union-search-skill 研究一下这玩意，能不能集成一下给阿黑用",
                platform="telegram",
                chat=SimpleNamespace(id="chat-9"),
                user=SimpleNamespace(id="user-9"),
            ),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"software_delivery"})

    result = await dispatcher.execute(
        name="software_delivery",
        args={},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured.get("action") == "skill_create"
    assert captured.get("repo_url") == "https://github.com/runningZ1/union-search-skill"
    assert captured.get("skill_name") == "union-search-skill"
    assert captured.get("notify_platform") == "telegram"
    assert captured.get("notify_chat_id") == "chat-9"
    assert captured.get("notify_user_id") == "user-9"


@pytest.mark.asyncio
async def test_manager_blocks_bash_when_software_delivery_intent(monkeypatch):
    async def append_event(_event: str):
        return None

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            return {"ok": True, "echo": kwargs}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-2",
        platform_name="telegram",
        task_id="task-3",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="这个技能有问题，帮我看日志并修复代码"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"bash", "software_delivery"})

    result = await dispatcher.execute(
        name="bash",
        args={"command": "ls"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is False
    assert result["error_code"] == "software_delivery_required"


def test_software_delivery_intent_ignores_plain_github_research():
    assert (
        ToolCallDispatcher._is_software_delivery_intent(
            "看一下github上glwlg/x-bot这个项目这两天更新了什么"
        )
        is False
    )
    assert (
        ToolCallDispatcher._is_software_delivery_intent(
            "帮我修复这个 GitHub issue 对应的代码问题"
        )
        is True
    )


@pytest.mark.asyncio
async def test_manager_allows_loaded_skill_cli_bash_when_request_mentions_code():
    captured = {}

    async def append_event(_event: str):
        return None

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            captured.update(dict(kwargs))
            return {"ok": True, "summary": "ok"}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-2",
        platform_name="telegram",
        task_id="task-3b",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="这个技能有问题，帮我看日志并修复代码"),
            user_data={
                "last_loaded_skill_dir": "/app/skills/builtin/worker_management",
                "last_loaded_skill_entrypoint": "scripts/execute.py",
            },
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"bash", "software_delivery"})

    result = await dispatcher.execute(
        name="bash",
        args={
            "command": (
                "cd /app/skills/builtin/worker_management "
                "&& python scripts/execute.py dispatch hi"
            )
        },
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["name"] == "bash"


@pytest.mark.asyncio
async def test_worker_allows_bash_even_when_request_mentions_code(monkeypatch):
    async def append_event(_event: str):
        return None

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            return {"ok": True, "echo": kwargs}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="worker::worker-main::u-3",
        platform_name="worker_kernel",
        task_id="task-4",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="修复代码并查看日志"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"bash", "software_delivery"})

    result = await dispatcher.execute(
        name="bash",
        args={"command": "ls"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_manager_allows_bash_for_non_coding_ops(monkeypatch):
    async def append_event(_event: str):
        return None

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            return {"ok": True, "echo": kwargs}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-6",
        platform_name="telegram",
        task_id="task-6",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="帮我看下容器日志和磁盘占用"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"bash", "software_delivery"})

    result = await dispatcher.execute(
        name="bash",
        args={"command": "docker compose ps"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_manager_blocks_write_to_repo_code_path():
    async def append_event(_event: str):
        return None

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            return {"ok": True, "echo": kwargs}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-10",
        platform_name="telegram",
        task_id="task-10",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="顺手把这个 skill 改一下"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"write", "software_delivery"})

    result = await dispatcher.execute(
        name="write",
        args={"path": "skills/builtin/web_search/SKILL.md", "content": "# patched"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is False
    assert result["error_code"] == "software_delivery_required"


@pytest.mark.asyncio
async def test_manager_allows_write_to_runtime_data_path():
    captured = {}

    async def append_event(_event: str):
        return None

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            captured.update(dict(kwargs))
            return {"ok": True, "echo": kwargs}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-11",
        platform_name="telegram",
        task_id="task-11",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="更新一下用户状态"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"write", "software_delivery"})

    result = await dispatcher.execute(
        name="write",
        args={"path": "data/users/u-11/profile.json", "content": "{}"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["name"] == "write"
    assert captured["args"]["path"] == "data/user/profile.json"


@pytest.mark.asyncio
async def test_manager_rewrites_legacy_user1_path_for_read_tool(monkeypatch):
    async def append_event(_event: str):
        return None

    captured = {}

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            captured.update(dict(kwargs or {}))
            return {"ok": True}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="257675041",
        platform_name="telegram",
        task_id="task-9",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="读取记忆"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"read"})

    result = await dispatcher.execute(
        name="read",
        args={"path": "data/users/user1/MEMORY.md", "start_line": 1, "max_lines": 10},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["args"]["path"] == "data/user/MEMORY.md"


@pytest.mark.asyncio
async def test_worker_rewrites_legacy_user1_path_to_single_user_root(monkeypatch):
    async def append_event(_event: str):
        return None

    captured = {}

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            captured.update(dict(kwargs or {}))
            return {"ok": True}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="worker::worker-main::257675041",
        platform_name="worker_kernel",
        task_id="task-10",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="读取记忆"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"read"})

    result = await dispatcher.execute(
        name="read",
        args={"path": "data/users/user1/MEMORY.md", "start_line": 1, "max_lines": 10},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["args"]["path"] == "data/user/MEMORY.md"


@pytest.mark.asyncio
async def test_bash_rewrites_legacy_user_path_to_single_user_root(monkeypatch):
    async def append_event(_event: str):
        return None

    captured = {}

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            captured.update(dict(kwargs or {}))
            return {"ok": True}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="257675041",
        platform_name="telegram",
        task_id="task-12",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="检查记忆目录",
                user=SimpleNamespace(id="257675041"),
                chat=SimpleNamespace(id="257675041"),
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
        args={"command": "ls -la /app/data/users/257675041/ && cat data/users/user1/MEMORY.md"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert "/app/data/user/" in captured["args"]["command"]
    assert "data/user/MEMORY.md" in captured["args"]["command"]
    assert "data/users/" not in captured["args"]["command"]
