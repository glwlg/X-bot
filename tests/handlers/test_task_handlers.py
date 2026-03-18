from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.task_inbox import task_inbox
from handlers import task_command as exported_task_command
from handlers.task_handlers import task_command


class _FakeUser:
    def __init__(self, user_id: str):
        self.id = user_id


class _FakeMessage:
    def __init__(self, text: str, user_id: str):
        self.text = text
        self.user = _FakeUser(user_id)


class _FakeContext:
    def __init__(self, text: str, user_id: str = "u-task"):
        self.message = _FakeMessage(text, user_id)
        self.replies: list[str] = []

    async def reply(self, text: str, **kwargs):
        self.replies.append(text)
        return SimpleNamespace(id="reply")


def _reset_task_inbox(tmp_path: Path) -> None:
    root = (tmp_path / "task_inbox").resolve()
    tasks_root = (root / "tasks").resolve()
    archive_root = (root / "archive").resolve()
    events_path = (root / "events.jsonl").resolve()
    tasks_root.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)
    task_inbox.persist = True
    task_inbox.root = root
    task_inbox.tasks_root = tasks_root
    task_inbox.archive_root = archive_root
    task_inbox.events_path = events_path
    task_inbox._loaded = False
    task_inbox._tasks = {}


@pytest.mark.asyncio
async def test_task_command_lists_recent_manager_tasks(monkeypatch, tmp_path):
    _reset_task_inbox(tmp_path)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr("handlers.task_handlers.check_permission_unified", _allow)

    first = await task_inbox.submit(
        source="user_chat",
        goal="修复 PR 冲突并重新提交",
        user_id="u-task",
    )
    second = await task_inbox.submit(
        source="heartbeat",
        goal="检查未完成任务并完成他们",
        user_id="u-task",
        metadata={
            "followup": {
                "done_when": "GitHub pull request merged",
                "refs": {"pr_url": "https://github.com/example/repo/pull/42"},
            }
        },
    )
    await task_inbox.update_status(first.task_id, "running", event="work_started")
    await task_inbox.update_status(
        second.task_id,
        "waiting_external",
        event="followup_waiting",
    )

    ctx = _FakeContext("/task", user_id="u-task")
    await task_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert "最近 10 个任务" in reply
    assert second.task_id in reply
    assert "waiting_external" in reply
    assert "GitHub pull request merged" in reply
    assert "pull/42" in reply
    assert "waiting_external | heartbeat" in reply


@pytest.mark.asyncio
async def test_task_command_recent_alias_and_limit_10(monkeypatch, tmp_path):
    _reset_task_inbox(tmp_path)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr("handlers.task_handlers.check_permission_unified", _allow)

    for idx in range(12):
        task = await task_inbox.submit(
            source="user_chat",
            goal=f"任务-{idx}",
            user_id="u-task",
        )
        await task_inbox.update_status(task.task_id, "completed", event="done")

    ctx = _FakeContext("/task recent", user_id="u-task")
    await task_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert reply.count("- `") == 10


@pytest.mark.asyncio
async def test_task_command_skips_heartbeat_tasks_by_default(monkeypatch, tmp_path):
    _reset_task_inbox(tmp_path)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr("handlers.task_handlers.check_permission_unified", _allow)

    user_task = await task_inbox.submit(
        source="user_chat",
        goal="用户发起的任务",
        user_id="u-task",
    )
    heartbeat_task = await task_inbox.submit(
        source="heartbeat",
        goal="heartbeat 跟进任务",
        user_id="u-task",
    )
    await task_inbox.update_status(user_task.task_id, "running", event="running")
    await task_inbox.update_status(
        heartbeat_task.task_id,
        "running",
        event="running",
    )

    ctx = _FakeContext("/task", user_id="u-task")
    await task_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert user_task.task_id in reply
    assert heartbeat_task.task_id not in reply


@pytest.mark.asyncio
async def test_task_command_keeps_heartbeat_followup_tasks(monkeypatch, tmp_path):
    _reset_task_inbox(tmp_path)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr("handlers.task_handlers.check_permission_unified", _allow)

    heartbeat_followup = await task_inbox.submit(
        source="heartbeat",
        goal="heartbeat 跟进 PR",
        user_id="u-task",
        metadata={
            "followup": {
                "done_when": "GitHub pull request merged",
            }
        },
    )
    await task_inbox.update_status(
        heartbeat_followup.task_id,
        "waiting_external",
        event="followup_waiting",
    )

    ctx = _FakeContext("/task", user_id="u-task")
    await task_command(ctx)

    assert ctx.replies
    assert heartbeat_followup.task_id in ctx.replies[-1]


@pytest.mark.asyncio
async def test_task_command_hides_completed_heartbeat_followup_tasks(
    monkeypatch, tmp_path
):
    _reset_task_inbox(tmp_path)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr("handlers.task_handlers.check_permission_unified", _allow)

    completed_followup = await task_inbox.submit(
        source="heartbeat",
        goal="heartbeat 已完成的 PR 跟进",
        user_id="u-task",
        metadata={
            "followup": {
                "done_when": "GitHub pull request merged",
            }
        },
    )
    await task_inbox.update_status(
        completed_followup.task_id,
        "completed",
        event="completed",
        result={"summary": "PR 已关闭，无需继续跟进。"},
        output={"text": "PR 已关闭，无需继续跟进。"},
    )

    ctx = _FakeContext("/task", user_id="u-task")
    await task_command(ctx)

    assert ctx.replies
    assert completed_followup.task_id not in ctx.replies[-1]


@pytest.mark.asyncio
async def test_task_command_rejects_unknown_subcommand(monkeypatch, tmp_path):
    _reset_task_inbox(tmp_path)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr("handlers.task_handlers.check_permission_unified", _allow)

    ctx = _FakeContext("/task foo", user_id="u-task")
    await task_command(ctx)

    assert ctx.replies
    assert "用法" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_task_command_open_only_lists_unfinished_tasks(monkeypatch, tmp_path):
    _reset_task_inbox(tmp_path)

    async def _allow(_ctx):
        return True

    monkeypatch.setattr("handlers.task_handlers.check_permission_unified", _allow)

    open_task = await task_inbox.submit(
        source="user_chat",
        goal="仍在处理中的任务",
        user_id="u-task",
    )
    done_task = await task_inbox.submit(
        source="user_chat",
        goal="已经完成的任务",
        user_id="u-task",
    )
    await task_inbox.update_status(open_task.task_id, "waiting_external", event="wait")
    await task_inbox.update_status(done_task.task_id, "completed", event="done")

    ctx = _FakeContext("/task open", user_id="u-task")
    await task_command(ctx)

    assert ctx.replies
    reply = ctx.replies[-1]
    assert open_task.task_id in reply
    assert done_task.task_id not in reply


def test_task_command_is_exported_from_handlers_package():
    assert exported_task_command is task_command


def test_main_registers_task_command():
    main_py = Path(__file__).resolve().parents[2] / "src" / "main.py"
    text = main_py.read_text(encoding="utf-8")

    assert 'on_command("task", task_command' in text
