import asyncio
import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("LLM_API_KEY", "test-key")

import core.todo_async_worker as worker_module


def _write_todo_file(
    task_dir: Path,
    user_id: str,
    task_id: str,
    goal: str,
    deliver_status: str = "pending",
) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    todo_path = task_dir / "TODO.md"
    todo_path.write_text(
        (
            "# TODO\n\n"
            f"- Task ID: `{task_id}`\n"
            f"- User: `{user_id}`\n"
            "- Created: `2026-02-14T11:40:00`\n"
            "- Last heartbeat: `2026-02-14T11:40:00`\n\n"
            "## Goal\n"
            f"> {goal}\n\n"
            "## Steps\n"
            "- [ ] `plan` Clarify goal and plan (pending, 2026-02-14T11:40:00)\n"
            "- [ ] `act` Execute tools/extensions (pending, 2026-02-14T11:40:00)\n"
            "- [ ] `verify` Verify outcome (pending, 2026-02-14T11:40:00)\n"
            f"- [ ] `deliver` Deliver final response ({deliver_status}, 2026-02-14T11:40:00)\n\n"
            "## Recent Events\n"
            "- (none)\n"
        ),
        encoding="utf-8",
    )
    (task_dir / "heartbeat.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "user_id": user_id,
                "updated_at": "2020-01-01T00:00:00",
                "events": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return todo_path


async def _wait_worker_idle(
    worker: worker_module.TodoAsyncWorker, timeout_sec: float = 2.0
):
    deadline = time.time() + timeout_sec
    while worker._running and time.time() < deadline:
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_todo_async_worker_processes_pending_todo(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime_tasks" / "u1" / "task-a"
    _write_todo_file(
        runtime_root, user_id="u1", task_id="task-a", goal="执行一个后台任务"
    )

    async def fake_handle_message(ctx, message_history):
        assert ctx.message.user.id == "u1"
        assert message_history[0]["parts"][0]["text"] == "执行一个后台任务"
        yield "done-by-daemon"

    monkeypatch.setattr(worker_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        worker_module.agent_orchestrator, "handle_message", fake_handle_message
    )

    worker = worker_module.TodoAsyncWorker()
    worker.enabled = True
    worker.stale_heartbeat_sec = 0

    await worker.process_once()
    await _wait_worker_idle(worker)

    result_path = runtime_root / "RESULT.md"
    assert result_path.exists()
    assert "done-by-daemon" in result_path.read_text(encoding="utf-8")

    heartbeat = json.loads(
        (runtime_root / "heartbeat.json").read_text(encoding="utf-8")
    )
    assert any("daemon:completed" in item for item in heartbeat.get("events", []))


@pytest.mark.asyncio
async def test_todo_async_worker_not_reprocess_unchanged_file(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime_tasks" / "u2" / "task-b"
    todo_path = _write_todo_file(
        runtime_root, user_id="u2", task_id="task-b", goal="重复保护任务"
    )

    async def fake_handle_message(ctx, message_history):
        yield "single-run"

    monkeypatch.setattr(worker_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        worker_module.agent_orchestrator, "handle_message", fake_handle_message
    )

    worker = worker_module.TodoAsyncWorker()
    worker.enabled = True
    worker.stale_heartbeat_sec = 0

    await worker.process_once()
    await _wait_worker_idle(worker)
    await worker.process_once()
    await _wait_worker_idle(worker)

    result_text = (runtime_root / "RESULT.md").read_text(encoding="utf-8")
    assert result_text.count("single-run") == 1

    todo_path.write_text(
        todo_path.read_text(encoding="utf-8") + "\n- external edit\n", encoding="utf-8"
    )

    await worker.process_once()
    await _wait_worker_idle(worker)
    result_text_after = (runtime_root / "RESULT.md").read_text(encoding="utf-8")
    assert result_text_after.count("single-run") == 2


@pytest.mark.asyncio
async def test_todo_async_worker_skips_completed_todo(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime_tasks" / "u3" / "task-c"
    _write_todo_file(
        runtime_root,
        user_id="u3",
        task_id="task-c",
        goal="已完成任务",
        deliver_status="done",
    )

    async def fake_handle_message(ctx, message_history):
        raise AssertionError("should not execute completed TODO")

    monkeypatch.setattr(worker_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        worker_module.agent_orchestrator, "handle_message", fake_handle_message
    )

    worker = worker_module.TodoAsyncWorker()
    worker.enabled = True
    worker.stale_heartbeat_sec = 0

    await worker.process_once()
    await _wait_worker_idle(worker)

    assert not (runtime_root / "RESULT.md").exists()
