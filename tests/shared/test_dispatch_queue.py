import pytest

from shared.contracts.dispatch import TaskResult
from shared.queue.dispatch_queue import DispatchQueue


@pytest.mark.asyncio
async def test_dispatch_queue_submit_claim_finish(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="run check",
        source="manager_dispatch",
        metadata={"session_id": "s-1"},
    )
    assert submitted.task_id
    assert submitted.status == "pending"

    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-daemon")
    assert claimed is not None
    assert claimed.task_id == submitted.task_id
    assert claimed.status == "running"
    assert claimed.claimed_by == "worker-daemon"

    result = TaskResult(
        task_id=claimed.task_id,
        worker_id="worker-main",
        ok=True,
        summary="done",
        payload={"text": "done"},
    )
    finished = await queue.finish_task(task_id=claimed.task_id, result=result)
    assert finished is not None
    assert finished.status == "done"

    recent = await queue.list_tasks(worker_id="worker-main", limit=5)
    assert recent
    assert recent[0].task_id == submitted.task_id
