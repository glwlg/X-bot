import pytest

from shared.contracts.dispatch import TaskResult
from shared.queue.dispatch_queue import DispatchQueue


@pytest.mark.asyncio
async def test_dispatch_queue_roundtrip_for_worker_tasks(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="echo hello",
        source="manager_dispatch",
        backend="shell",
        metadata={
            "platform": "telegram",
            "chat_id": "c1",
            "session_id": "s1",
            "user_id": "u1",
        },
    )
    assert submitted.task_id

    claimed = await queue.claim_next(claimer="test-daemon", worker_id="worker-main")
    assert claimed is not None
    assert claimed.task_id == submitted.task_id
    assert claimed.status == "running"

    finished = await queue.finish_task(
        task_id=submitted.task_id,
        result=TaskResult(
            task_id=submitted.task_id,
            worker_id="worker-main",
            ok=True,
            summary="ok",
            payload={"text": "ok"},
        ),
    )
    assert finished is not None
    assert finished.status == "done"

    undelivered = await queue.list_undelivered(limit=10)
    assert len(undelivered) == 1
    assert undelivered[0].task_id == submitted.task_id

    marked = await queue.mark_delivered(submitted.task_id)
    assert marked is True
    assert await queue.list_undelivered(limit=10) == []


@pytest.mark.asyncio
async def test_dispatch_queue_cancel_for_user_handles_pending_and_running(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    queue = DispatchQueue()

    running_task = await queue.submit_task(
        worker_id="worker-main",
        instruction="echo running",
        source="manager_dispatch",
        backend="shell",
        metadata={"user_id": "u-stop", "session_id": "s-running"},
    )
    claimed = await queue.claim_next(claimer="daemon", worker_id="worker-main")
    assert claimed is not None
    assert claimed.task_id == running_task.task_id

    pending_task = await queue.submit_task(
        worker_id="worker-main",
        instruction="echo pending",
        source="manager_dispatch",
        backend="shell",
        metadata={"user_id": "u-stop", "session_id": "s-pending"},
    )
    await queue.submit_task(
        worker_id="worker-main",
        instruction="echo untouched",
        source="manager_dispatch",
        backend="shell",
        metadata={"user_id": "u-other", "session_id": "s-other"},
    )

    cancelled = await queue.cancel_for_user(
        user_id="u-stop",
        reason="cancelled_by_stop_command",
        include_running=True,
    )
    assert cancelled["pending_cancelled"] == 1
    assert cancelled["running_signaled"] == 1
    assert running_task.task_id in cancelled["job_ids"]
    assert pending_task.task_id in cancelled["job_ids"]
