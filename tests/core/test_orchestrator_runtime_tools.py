import time
from types import SimpleNamespace

import pytest
from core.orchestrator_runtime_tools import ToolCallDispatcher
import core.orchestrator_runtime_tools as runtime_tools_module


def test_runtime_tool_dispatcher_no_longer_uses_legacy_extension_executor():
    assert hasattr(runtime_tools_module, "skill_loader")
    assert not hasattr(runtime_tools_module, "extension_tools")
    assert not hasattr(runtime_tools_module, "skill_arg_planner")


class _FakeDeliveryCtx:
    def __init__(self, *, platform: str = "telegram"):
        self.message = SimpleNamespace(
            text="把文件发给我",
            platform=platform,
        )
        self.user_data = {}
        self.documents: list[dict[str, object]] = []
        self.photos: list[dict[str, object]] = []
        self.videos: list[dict[str, object]] = []
        self.audios: list[dict[str, object]] = []

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        self.documents.append(
            {
                "document": document,
                "filename": filename,
                "caption": caption,
                "kwargs": dict(kwargs),
            }
        )
        return SimpleNamespace(id="doc")

    async def reply_photo(self, photo, caption=None, **kwargs):
        self.photos.append({"photo": photo, "caption": caption, "kwargs": dict(kwargs)})
        return SimpleNamespace(id="photo")

    async def reply_video(self, video, caption=None, **kwargs):
        self.videos.append({"video": video, "caption": caption, "kwargs": dict(kwargs)})
        return SimpleNamespace(id="video")

    async def reply_audio(self, audio, caption=None, **kwargs):
        self.audios.append({"audio": audio, "caption": caption, "kwargs": dict(kwargs)})
        return SimpleNamespace(id="audio")


def test_runtime_tool_retry_policy_uses_structured_error_codes_only():
    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-struct-1",
        platform_name="telegram",
        task_id="task-struct-1",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(message=SimpleNamespace(text="test"), user_data={}),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=lambda *_args, **_kwargs: None,
    )

    assert (
        dispatcher._should_retry_extension(
            {
                "ok": False,
                "failure_mode": "recoverable",
                "error_code": "invalid_args",
                "message": "缺少参数",
            }
        )
        is True
    )
    assert (
        dispatcher._should_retry_extension(
            {
                "ok": False,
                "failure_mode": "recoverable",
                "error_code": "",
                "message": "缺少参数",
            }
        )
        is False
    )


@pytest.mark.asyncio
async def test_ikaros_allows_bash_for_coding_requests_without_legacy_pipeline(
    monkeypatch,
):
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names(
        {"bash", "coding_session", "repo_workspace", "git_ops", "gh_cli"}
    )

    result = await dispatcher.execute(
        name="bash",
        args={"command": "ls"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_ikaros_can_send_local_file_via_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_FILE_DELIVERY_ALLOWED_ROOTS", str(tmp_path))
    target = (tmp_path / "README.md").resolve()
    target.write_text("# demo\n", encoding="utf-8")

    async def append_event(_event: str):
        return None

    ctx = _FakeDeliveryCtx(platform="telegram")
    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-send-1",
        platform_name="telegram",
        task_id="task-send-1",
        task_inbox_id="",
        task_workspace_root=str(tmp_path),
        ctx=ctx,
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"send_local_file"})

    result = await dispatcher.execute(
        name="send_local_file",
        args={"path": "README.md", "caption": "请查收"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert result["terminal"] is False
    assert ctx.documents == []
    assert ctx.photos == []
    assert ctx.videos == []
    assert ctx.audios == []
    assert result["files"] == result["payload"]["files"]
    assert result["files"][0]["path"] == str(target)
    assert result["files"][0]["filename"] == "README.md"
    assert result["files"][0]["caption"] == "请查收"


@pytest.mark.asyncio
async def test_complete_task_dispatcher_emits_structured_terminal_payload(tmp_path):
    async def append_event(_event: str):
        return None

    demo_file = (tmp_path / "demo.txt").resolve()
    demo_file.write_text("done\n", encoding="utf-8")

    dispatcher = ToolCallDispatcher(
        runtime_user_id="u-complete-1",
        platform_name="telegram",
        task_id="task-complete-1",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(message=SimpleNamespace(text="完成任务"), user_data={}),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"complete_task"})

    result = await dispatcher.execute(
        name="complete_task",
        args={
            "status": "done",
            "text": "任务已完成。",
            "summary": "done summary",
            "files": [{"path": str(demo_file), "filename": "demo.txt"}],
            "followup": {"ignored": True},
        },
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert result["terminal"] is True
    assert result["task_outcome"] == "done"
    assert result["completion_signal"]["explicit"] is True
    assert result["completion_signal"]["status"] == "done"
    assert result["payload"]["files"][0]["filename"] == "demo.txt"
    assert result["payload"]["text"] == "任务已完成。"


@pytest.mark.asyncio
async def test_ikaros_allows_loaded_skill_cli_bash_when_request_mentions_code():
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
                "last_loaded_skill_dir": "/app/extension/skills/builtin/deployment_manager",
                "last_loaded_skill_entrypoint": "scripts/execute.py",
            },
        ),
        runtime=object(),
        tool_broker=_FakeToolBroker(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names(
        {"bash", "coding_session", "repo_workspace", "git_ops", "gh_cli"}
    )

    result = await dispatcher.execute(
        name="bash",
        args={
            "command": (
                "cd /app/extension/skills/builtin/deployment_manager "
                "&& python scripts/execute.py help"
            )
        },
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["name"] == "bash"


@pytest.mark.asyncio
async def test_subagent_allows_bash_even_when_request_mentions_code(monkeypatch):
    async def append_event(_event: str):
        return None

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            return {"ok": True, "echo": kwargs}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="subagent::subagent-main::u-3",
        platform_name="subagent_kernel",
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names(
        {"bash", "coding_session", "repo_workspace", "git_ops", "gh_cli"}
    )

    result = await dispatcher.execute(
        name="bash",
        args={"command": "ls"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_ikaros_allows_bash_for_non_coding_ops(monkeypatch):
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names(
        {"bash", "coding_session", "repo_workspace", "git_ops", "gh_cli"}
    )

    result = await dispatcher.execute(
        name="bash",
        args={"command": "docker compose ps"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_ikaros_allows_write_to_repo_code_path_when_policy_allows():
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names(
        {"write", "coding_session", "repo_workspace", "git_ops"}
    )

    result = await dispatcher.execute(
        name="write",
        args={"path": "extension/skills/builtin/web_search/SKILL.md", "content": "# patched"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_ikaros_passes_current_runtime_data_path_through_unchanged():
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names(
        {"write", "coding_session", "repo_workspace", "git_ops"}
    )

    result = await dispatcher.execute(
        name="write",
        args={"path": "data/user/profile.json", "content": "{}"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["name"] == "write"
    assert captured["args"]["path"] == "data/user/profile.json"


@pytest.mark.asyncio
async def test_ikaros_keeps_current_memory_path_for_read_tool(monkeypatch):
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"read"})

    result = await dispatcher.execute(
        name="read",
        args={"path": "data/user/MEMORY.md", "start_line": 1, "max_lines": 10},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["args"]["path"] == "data/user/MEMORY.md"


@pytest.mark.asyncio
async def test_subagent_keeps_current_memory_path_unchanged(monkeypatch):
    async def append_event(_event: str):
        return None

    captured = {}

    class _FakeToolBroker:
        async def execute_core_tool(self, **kwargs):
            captured.update(dict(kwargs or {}))
            return {"ok": True}

    dispatcher = ToolCallDispatcher(
        runtime_user_id="subagent::subagent-main::257675041",
        platform_name="subagent_kernel",
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"read"})

    result = await dispatcher.execute(
        name="read",
        args={"path": "data/user/MEMORY.md", "start_line": 1, "max_lines": 10},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert captured["args"]["path"] == "data/user/MEMORY.md"


@pytest.mark.asyncio
async def test_bash_keeps_current_memory_path_unchanged(monkeypatch):
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
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"bash"})

    result = await dispatcher.execute(
        name="bash",
        args={"command": "ls -la /app/data/user/ && cat data/user/MEMORY.md"},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert "/app/data/user/" in captured["args"]["command"]
    assert "cat data/user/MEMORY.md" in captured["args"]["command"]
