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

    running = await store.list_running(limit=10)
    assert running
    assert running[0]["job_id"] == submitted["job_id"]

    progress_updated = await store.update_running_progress(
        submitted["job_id"],
        progress={
            "summary": "已完成 ext_web_search",
            "running_tool": "ext_web_browser",
        },
    )
    assert progress_updated is True

    running_after_progress = await store.list_running(limit=10)
    assert running_after_progress
    progress = running_after_progress[0]["metadata"].get("progress")
    assert isinstance(progress, dict)
    assert progress.get("running_tool") == "ext_web_browser"

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


@pytest.mark.asyncio
async def test_worker_task_file_store_recover_running_tasks(tmp_path, monkeypatch):
    workers_root = (tmp_path / "workers").resolve()
    meta_path = (tmp_path / "WORKERS.json").resolve()
    workers_root.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker_registry, "root", workers_root)
    monkeypatch.setattr(worker_registry, "meta_path", meta_path)

    store = WorkerTaskFileStore()
    submitted = await store.submit(
        worker_id="worker-main",
        instruction="echo restart",
        source="manager_dispatch",
        backend="shell",
        metadata={
            "platform": "telegram",
            "chat_id": "c2",
            "session_id": "s2",
        },
    )

    claimed = await store.claim_next(claimer="old-daemon", worker_id="worker-main")
    assert claimed is not None
    assert claimed["job_id"] == submitted["job_id"]

    progress_updated = await store.update_running_progress(
        submitted["job_id"],
        progress={
            "summary": "正在执行 ext_deep_research",
            "running_tool": "ext_deep_research",
        },
    )
    assert progress_updated is True

    recovered = await store.recover_running_tasks(worker_id="worker-main")
    assert recovered == 1
    assert await store.list_running(limit=10) == []

    reclaimed = await store.claim_next(claimer="new-daemon", worker_id="worker-main")
    assert reclaimed is not None
    assert reclaimed["job_id"] == submitted["job_id"]
    assert reclaimed["status"] == "running"
    assert "progress" not in (reclaimed.get("metadata") or {})


@pytest.mark.asyncio
async def test_worker_task_file_store_cancel_for_user_handles_pending_and_running(
    tmp_path, monkeypatch
):
    workers_root = (tmp_path / "workers").resolve()
    meta_path = (tmp_path / "WORKERS.json").resolve()
    workers_root.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker_registry, "root", workers_root)
    monkeypatch.setattr(worker_registry, "meta_path", meta_path)

    store = WorkerTaskFileStore()

    running_job = await store.submit(
        worker_id="worker-main",
        instruction="echo running",
        source="manager_dispatch",
        backend="shell",
        metadata={"user_id": "u-stop", "session_id": "s-running"},
    )
    claimed = await store.claim_next(claimer="daemon", worker_id="worker-main")
    assert claimed is not None
    assert claimed["job_id"] == running_job["job_id"]

    pending_job = await store.submit(
        worker_id="worker-main",
        instruction="echo pending",
        source="manager_dispatch",
        backend="shell",
        metadata={"user_id": "u-stop", "session_id": "s-pending"},
    )
    untouched_job = await store.submit(
        worker_id="worker-main",
        instruction="echo untouched",
        source="manager_dispatch",
        backend="shell",
        metadata={"user_id": "u-other", "session_id": "s-other"},
    )

    cancelled = await store.cancel_for_user(
        user_id="u-stop",
        reason="cancelled_by_stop_command",
        include_running=True,
    )
    assert cancelled["pending_cancelled"] == 1
    assert cancelled["running_signaled"] == 1
    assert running_job["job_id"] in cancelled["job_ids"]
    assert pending_job["job_id"] in cancelled["job_ids"]

    assert await store.is_cancel_requested(running_job["job_id"]) is True

    running_rows = await store.list_running(limit=10)
    running_ids = {row.get("job_id") for row in running_rows}
    assert running_job["job_id"] in running_ids
    running_row = next(
        row for row in running_rows if row.get("job_id") == running_job["job_id"]
    )
    metadata = running_row.get("metadata") or {}
    assert metadata.get("cancel_requested") is True
    assert metadata.get("suppress_delivery") is True

    next_claimed = await store.claim_next(claimer="daemon-2", worker_id="worker-main")
    assert next_claimed is not None
    assert next_claimed["job_id"] == untouched_job["job_id"]

    history_path = Path(workers_root / "worker-main" / "TASK_HISTORY.md")
    history_text = history_path.read_text(encoding="utf-8")
    assert pending_job["job_id"] in history_text
    assert "cancelled_by_user" in history_text
