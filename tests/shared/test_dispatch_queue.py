import asyncio

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


@pytest.mark.asyncio
async def test_finish_task_does_not_override_cancelled_status(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="run check",
        source="manager_dispatch",
        metadata={"user_id": "u-stop"},
    )
    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-daemon")
    assert claimed is not None

    cancelled = await queue.cancel_for_user(
        user_id="u-stop",
        reason="cancelled_by_stop_command",
        include_running=True,
    )
    assert cancelled["running_signaled"] == 1

    result = TaskResult(
        task_id=submitted.task_id,
        worker_id="worker-main",
        ok=True,
        summary="done",
        payload={"text": "done"},
    )
    finished = await queue.finish_task(task_id=submitted.task_id, result=result)
    assert finished is not None
    assert finished.status == "cancelled"
    assert finished.error == "cancelled_by_stop_command"


@pytest.mark.asyncio
async def test_claim_next_is_atomic_under_concurrency(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    await queue.submit_task(
        worker_id="worker-main",
        instruction="task-1",
        source="manager_dispatch",
    )
    await queue.submit_task(
        worker_id="worker-main",
        instruction="task-2",
        source="manager_dispatch",
    )

    # Widen race windows for legacy implementations that split read/write locks.
    original_write_all = queue.tasks.write_all

    async def delayed_write_all(rows):
        await asyncio.sleep(0.05)
        return await original_write_all(rows)

    monkeypatch.setattr(queue.tasks, "write_all", delayed_write_all)

    claim_a, claim_b = await asyncio.gather(
        queue.claim_next(worker_id="worker-main", claimer="worker-a"),
        queue.claim_next(worker_id="worker-main", claimer="worker-b"),
    )

    assert claim_a is not None
    assert claim_b is not None
    assert claim_a.task_id != claim_b.task_id

    running_rows = await queue.list_running(limit=10)
    running_ids = {row.task_id for row in running_rows}
    assert claim_a.task_id in running_ids
    assert claim_b.task_id in running_ids


@pytest.mark.asyncio
async def test_list_undelivered_scans_full_table(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    root = await queue.submit_task(
        worker_id="worker-main",
        instruction="first",
        source="manager_dispatch",
    )
    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-a")
    assert claimed is not None
    done_result = TaskResult(
        task_id=claimed.task_id,
        worker_id="worker-main",
        ok=True,
        summary="done",
        payload={"text": "done"},
    )
    await queue.finish_task(task_id=claimed.task_id, result=done_result)

    for idx in range(30):
        await queue.submit_task(
            worker_id="worker-main",
            instruction=f"new-{idx}",
            source="manager_dispatch",
        )

    undelivered = await queue.list_undelivered(limit=5)
    assert any(task.task_id == root.task_id for task in undelivered)


@pytest.mark.asyncio
async def test_claim_next_recovers_stale_running_task(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISPATCH_RUNNING_STALE_SEC", "60")
    monkeypatch.setenv("DISPATCH_CLAIM_MAX_RETRIES", "3")
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="stale-task",
        source="manager_dispatch",
    )
    first_claim = await queue.claim_next(worker_id="worker-main", claimer="worker-a")
    assert first_claim is not None
    assert first_claim.task_id == submitted.task_id

    rows = await queue.tasks.read_all()
    rows[0]["started_at"] = "2000-01-01T00:00:00+00:00"
    rows[0]["updated_at"] = "2000-01-01T00:00:00+00:00"
    await queue.tasks.write_all(rows)

    second_claim = await queue.claim_next(worker_id="worker-main", claimer="worker-b")
    assert second_claim is not None
    assert second_claim.task_id == submitted.task_id
    assert second_claim.claimed_by == "worker-b"

    task_row = await queue.get_task(submitted.task_id)
    assert task_row is not None
    assert task_row.retry_count == 1
    recovery = dict((task_row.metadata or {}).get("_claim_recovery") or {})
    assert recovery.get("state") == "stale_claim_recovered"


@pytest.mark.asyncio
async def test_claim_next_marks_stale_running_failed_after_retry_exhausted(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISPATCH_RUNNING_STALE_SEC", "60")
    monkeypatch.setenv("DISPATCH_CLAIM_MAX_RETRIES", "1")
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="stale-task",
        source="manager_dispatch",
    )
    first_claim = await queue.claim_next(worker_id="worker-main", claimer="worker-a")
    assert first_claim is not None

    rows = await queue.tasks.read_all()
    rows[0]["started_at"] = "2000-01-01T00:00:00+00:00"
    rows[0]["updated_at"] = "2000-01-01T00:00:00+00:00"
    await queue.tasks.write_all(rows)

    follow_up = await queue.claim_next(worker_id="worker-main", claimer="worker-b")
    assert follow_up is None

    task_row = await queue.get_task(submitted.task_id)
    assert task_row is not None
    assert task_row.status == "failed"
    assert task_row.error == "worker_claim_stale_timeout"
    assert task_row.retry_count == 1
    recovery = dict((task_row.metadata or {}).get("_claim_recovery") or {})
    assert recovery.get("state") == "stale_claim_failed"


@pytest.mark.asyncio
async def test_finish_task_uses_task_snapshot_when_result_append_fails(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="run check",
        source="manager_dispatch",
    )
    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-a")
    assert claimed is not None

    async def broken_append(_row):
        raise OSError("disk full")

    monkeypatch.setattr(queue.results, "append", broken_append)

    result = TaskResult(
        task_id=submitted.task_id,
        worker_id="worker-main",
        ok=True,
        summary="done",
        payload={"text": "done"},
    )
    finished = await queue.finish_task(task_id=submitted.task_id, result=result)
    assert finished is not None
    assert finished.status == "done"

    task_row = await queue.get_task(submitted.task_id)
    assert task_row is not None
    metadata = dict(task_row.metadata or {})
    assert isinstance(metadata.get("_latest_result"), dict)
    persist_error = dict(metadata.get("_result_persist_error") or {})
    assert "disk full" in str(persist_error.get("error") or "")

    latest = await queue.latest_result(submitted.task_id)
    assert latest is not None
    assert latest.ok is True
    assert latest.summary == "done"


@pytest.mark.asyncio
async def test_latest_result_prefers_results_table_over_task_snapshot(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="run check",
        source="manager_dispatch",
    )
    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-a")
    assert claimed is not None

    result = TaskResult(
        task_id=submitted.task_id,
        worker_id="worker-main",
        ok=True,
        summary="from-log",
        payload={"text": "from-log"},
    )
    await queue.finish_task(task_id=submitted.task_id, result=result)

    rows = await queue.tasks.read_all()
    rows[0]["metadata"]["_latest_result"]["summary"] = "from-snapshot"
    rows[0]["metadata"]["_latest_result"]["payload"] = {"text": "from-snapshot"}
    await queue.tasks.write_all(rows)

    latest = await queue.latest_result(submitted.task_id)
    assert latest is not None
    assert latest.summary == "from-log"
    assert str(latest.payload.get("text") or "") == "from-log"


@pytest.mark.asyncio
async def test_delivery_health_reports_retry_dead_letter_and_persist_error(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    async def _finish(worker_id: str, *, ok: bool, instruction: str):
        submitted = await queue.submit_task(
            worker_id=worker_id,
            instruction=instruction,
            source="manager_dispatch",
        )
        claimed = await queue.claim_next(worker_id=worker_id, claimer="worker-a")
        assert claimed is not None
        result = TaskResult(
            task_id=submitted.task_id,
            worker_id=worker_id,
            ok=ok,
            summary="done" if ok else "failed",
            error="" if ok else "failed",
            payload={"text": "done" if ok else "failed"},
        )
        await queue.finish_task(task_id=submitted.task_id, result=result)
        return submitted

    task_done = await _finish("worker-main", ok=True, instruction="done-task")
    task_dead = await _finish("worker-main", ok=False, instruction="dead-task")
    task_retry = await _finish("worker-main", ok=False, instruction="retry-task")
    task_delivered = await _finish(
        "worker-main",
        ok=True,
        instruction="delivered-task",
    )
    assert await queue.mark_delivered(task_delivered.task_id) is True

    rows = await queue.tasks.read_all()
    for row in rows:
        task_id = str(row.get("task_id") or "")
        metadata = dict(row.get("metadata") or {})
        if task_id == task_done.task_id:
            metadata["_result_persist_error"] = {
                "error": "disk full",
                "updated_at": "2026-03-01T12:00:00+08:00",
            }
        elif task_id == task_dead.task_id:
            metadata["_relay"] = {
                "attempts": 6,
                "state": "dead_letter",
                "last_error": "delivery_failed",
                "dead_letter_at": "2026-03-01T12:05:00+08:00",
            }
        elif task_id == task_retry.task_id:
            metadata["_relay"] = {
                "attempts": 2,
                "state": "retrying",
                "last_error": "missing_delivery_target",
                "next_retry_at": "2099-01-01T00:00:00+00:00",
            }
        row["metadata"] = metadata
    await queue.tasks.write_all(rows)

    health = await queue.delivery_health(
        worker_id="worker-main",
        dead_letter_limit=10,
    )
    assert int(health.get("undelivered") or 0) == 3
    assert int(health.get("retrying") or 0) == 1
    assert int(health.get("dead_letter") or 0) == 1
    assert int(health.get("result_persist_error") or 0) == 1

    recent_dead = list(health.get("recent_dead_letters") or [])
    assert len(recent_dead) == 1
    assert str(recent_dead[0].get("task_id") or "") == task_dead.task_id


@pytest.mark.asyncio
async def test_delivery_health_worker_filter(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    async def _finish(worker_id: str, instruction: str):
        submitted = await queue.submit_task(
            worker_id=worker_id,
            instruction=instruction,
            source="manager_dispatch",
        )
        claimed = await queue.claim_next(worker_id=worker_id, claimer="worker-a")
        assert claimed is not None
        result = TaskResult(
            task_id=submitted.task_id,
            worker_id=worker_id,
            ok=False,
            summary="failed",
            error="failed",
            payload={"text": "failed"},
        )
        await queue.finish_task(task_id=submitted.task_id, result=result)
        return submitted

    worker_a_task = await _finish("worker-a", "task-a")
    worker_b_task = await _finish("worker-b", "task-b")

    rows = await queue.tasks.read_all()
    for row in rows:
        task_id = str(row.get("task_id") or "")
        metadata = dict(row.get("metadata") or {})
        if task_id == worker_a_task.task_id:
            metadata["_relay"] = {
                "state": "dead_letter",
                "attempts": 3,
                "last_error": "delivery_failed",
                "dead_letter_at": "2026-03-01T12:00:00+08:00",
            }
        elif task_id == worker_b_task.task_id:
            metadata["_relay"] = {
                "state": "dead_letter",
                "attempts": 2,
                "last_error": "delivery_failed",
                "dead_letter_at": "2026-03-01T12:01:00+08:00",
            }
        row["metadata"] = metadata
    await queue.tasks.write_all(rows)

    health_a = await queue.delivery_health(worker_id="worker-a", dead_letter_limit=10)
    assert int(health_a.get("dead_letter") or 0) == 1
    recent_dead = list(health_a.get("recent_dead_letters") or [])
    assert len(recent_dead) == 1
    assert str(recent_dead[0].get("task_id") or "") == worker_a_task.task_id
    assert str(recent_dead[0].get("worker_id") or "") == "worker-a"


@pytest.mark.asyncio
async def test_requeue_dead_letter_clears_relay_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="dead-letter-task",
        source="manager_dispatch",
    )
    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-a")
    assert claimed is not None
    await queue.finish_task(
        task_id=submitted.task_id,
        result=TaskResult(
            task_id=submitted.task_id,
            worker_id="worker-main",
            ok=False,
            summary="failed",
            error="failed",
            payload={"text": "failed"},
        ),
    )

    rows = await queue.tasks.read_all()
    rows[0]["metadata"]["_relay"] = {
        "state": "dead_letter",
        "attempts": 6,
        "last_error": "delivery_failed",
        "dead_letter_at": "2026-03-01T12:00:00+08:00",
    }
    await queue.tasks.write_all(rows)

    retried = await queue.requeue_dead_letter(
        task_id=submitted.task_id,
        reason="manual_operator_retry",
    )
    assert retried["ok"] is True
    assert retried["retried"] is True
    assert retried["task_id"] == submitted.task_id

    task_row = await queue.get_task(submitted.task_id)
    assert task_row is not None
    metadata = dict(task_row.metadata or {})
    assert "_relay" not in metadata
    history = list(metadata.get("_relay_requeue_history") or [])
    assert len(history) == 1
    assert str(history[0].get("reason") or "") == "manual_operator_retry"
    assert int(history[0].get("previous_attempts") or 0) == 6


@pytest.mark.asyncio
async def test_requeue_dead_letter_rejects_non_dead_letter(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()

    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="normal-task",
        source="manager_dispatch",
    )
    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-a")
    assert claimed is not None
    await queue.finish_task(
        task_id=submitted.task_id,
        result=TaskResult(
            task_id=submitted.task_id,
            worker_id="worker-main",
            ok=True,
            summary="done",
            payload={"text": "done"},
        ),
    )

    retried = await queue.requeue_dead_letter(task_id=submitted.task_id)
    assert retried["ok"] is False
    assert retried["retried"] is False
    assert retried["error"] == "not_dead_letter"
