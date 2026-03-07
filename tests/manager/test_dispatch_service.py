import pytest

import manager.dispatch.service as service_module


class _FakeRegistry:
    def __init__(self):
        self.rows = [
            {
                "id": "worker-main",
                "name": "Main Worker",
                "status": "ready",
                "backend": "core-agent",
                "capabilities": ["code", "rss"],
                "summary": "通用执行",
            }
        ]

    async def list_workers(self):
        return list(self.rows)

    async def get_worker(self, worker_id: str):
        for row in self.rows:
            if row["id"] == worker_id:
                return dict(row)
        return None

    async def ensure_default_worker(self):
        return dict(self.rows[0])


class _FakeQueue:
    def __init__(self):
        self.last_metadata = {}

    async def submit_task(
        self,
        *,
        worker_id: str,
        instruction: str,
        source: str,
        backend: str = "",
        metadata: dict | None = None,
    ):
        _ = source
        _ = backend
        self.last_metadata = dict(metadata or {})
        return type(
            "_Queued",
            (),
            {
                "task_id": "tsk-queued-1",
                "worker_id": worker_id,
                "instruction": instruction,
            },
        )()


class _FakeWorkerTaskStore:
    def __init__(self):
        self.calls = []

    async def upsert_task(self, **kwargs):
        self.calls.append(dict(kwargs))
        return dict(kwargs)


@pytest.mark.asyncio
async def test_manager_dispatch_service_dispatches_async_task(monkeypatch):
    monkeypatch.setattr(service_module, "worker_registry", _FakeRegistry())
    fake_queue = _FakeQueue()
    fake_store = _FakeWorkerTaskStore()
    monkeypatch.setattr(service_module, "dispatch_queue", fake_queue)
    monkeypatch.setattr(service_module, "worker_task_store", fake_store)

    result = await service_module.manager_dispatch_service.dispatch_worker(
        instruction="请处理这个任务",
        metadata={"session_id": "session-1"},
    )

    assert result["ok"] is True
    assert result["task_id"] == "tsk-queued-1"
    assert result["terminal"] is False
    assert result["task_outcome"] == "partial"
    assert fake_queue.last_metadata.get("program_id") == "default-worker"
    assert fake_queue.last_metadata.get("program_version") == "v1"
    assert fake_queue.last_metadata.get("worker_name") == "Main Worker"
    assert (
        fake_queue.last_metadata.get("dispatch_component") == "manager_dispatch_service"
    )
    assert len(fake_store.calls) == 1
    assert fake_store.calls[0]["task_id"] == "tsk-queued-1"
    assert fake_store.calls[0]["status"] == "queued"
