import json
import os
import time

import pytest

from shared.queue.jsonl_queue import FileLock, JsonlTable


@pytest.mark.asyncio
async def test_file_lock_recovers_dead_pid_lock(tmp_path):
    lock_path = tmp_path / "tasks.jsonl.lock"
    lock_path.write_text("pid=999999\n", encoding="utf-8")

    async with FileLock(lock_path, timeout_sec=0.6, poll_sec=0.01):
        raw = lock_path.read_text(encoding="utf-8")
        assert f"pid={os.getpid()}" in raw

    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_jsonl_table_read_all_recovers_stale_legacy_lock(tmp_path):
    table_path = tmp_path / "tasks.jsonl"
    table_path.write_text(json.dumps({"task_id": "tsk-1"}) + "\n", encoding="utf-8")

    lock_path = table_path.with_suffix(table_path.suffix + ".lock")
    lock_path.write_text("legacy-lock\n", encoding="utf-8")
    old = time.time() - 1000
    os.utime(lock_path, (old, old))

    table = JsonlTable(str(table_path))
    rows = await table.read_all()

    assert rows == [{"task_id": "tsk-1"}]
