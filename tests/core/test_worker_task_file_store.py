from pathlib import Path

import pytest

from core.worker_store import worker_registry
from worker_runtime.task_file_store import WorkerTaskFileStore


@pytest.mark.asyncio
async def test_worker_task_file_store_roundtrip(tmp_path, monkeypatch):
    workers_root = (tmp_path / "workers").resolve()
    meta_path = (tmp_path / "WORKERS.json").resolve()
    workers_root.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker_registry, "root", workers_root)
    monkeypatch.setattr(worker_registry, "meta_path", meta_path)

    store = WorkerTaskFileStore()

    submitted = await store.submit(
        worker_id="worker-main",
        instruction="echo hello",
        source="manager_dispatch",
        backend="shell",
        metadata={
            "platform": "telegram",
            "chat_id": "c1",
            "session_id": "s1",
        },
    )
    assert submitted["job_id"]
    assert submitted["session_id"] == "s1"

    claimed = await store.claim_next(claimer="test-daemon", worker_id="worker-main")
    assert claimed is not None
    assert claimed["job_id"] == submitted["job_id"]
    assert claimed["status"] == "running"

    finished = await store.finish(
        submitted["job_id"],
        ok=True,
        result={
            "ok": True,
            "summary": "```python\nprint('ok')\n```",
            "text": "```python\nprint('ok')\n```",
        },
    )
    assert finished is not None
    assert finished["status"] == "done"

    undelivered = await store.list_undelivered(limit=10)
    assert len(undelivered) == 1
    assert undelivered[0]["job_id"] == submitted["job_id"]

    marked = await store.mark_delivered(submitted["job_id"], detail="delivered")
    assert marked is True
    assert await store.list_undelivered(limit=10) == []

    task_path = Path(workers_root / "worker-main" / "TASK.md")
    history_path = Path(workers_root / "worker-main" / "TASK_HISTORY.md")
    assert task_path.exists()
    assert history_path.exists()
    history_text = history_path.read_text(encoding="utf-8")
    assert submitted["job_id"] in history_text
