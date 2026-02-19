import asyncio
import os
from contextlib import suppress

import pytest

os.environ.setdefault("LLM_API_KEY", "test-key")

from core.task_manager import TaskManager


@pytest.mark.asyncio
async def test_task_manager_heartbeat_and_todo_path(tmp_path):
    manager = TaskManager()
    task = asyncio.create_task(asyncio.sleep(5))

    await manager.register_task("u1", task, description="test-task")
    assert manager.set_todo_path("u1", str(tmp_path / "TODO.md")) is True
    assert manager.heartbeat("u1", "turn:1") is True

    info = manager.get_task_info("u1")
    assert info is not None
    assert info["task_id"]
    assert info["last_heartbeat_note"] == "turn:1"
    assert info["todo_path"].endswith("TODO.md")
    assert info["heartbeat_age_seconds"] >= 0

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    manager.unregister_task("u1")
