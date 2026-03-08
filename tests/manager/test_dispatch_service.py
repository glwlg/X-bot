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
        self.last_priority = 0
        self.metrics = {}

    async def submit_task(
        self,
        *,
        worker_id: str,
        instruction: str,
        source: str,
        backend: str = "",
        priority: int = 0,
        metadata: dict | None = None,
    ):
        _ = source
        _ = backend
        self.last_metadata = dict(metadata or {})
        self.last_priority = int(priority or 0)
        return type(
            "_Queued",
            (),
            {
                "task_id": "tsk-queued-1",
                "worker_id": worker_id,
                "instruction": instruction,
            },
        )()

    async def worker_metrics(self, *, worker_id: str = "", limit: int = 200):
        _ = limit
        return dict(
            self.metrics.get(worker_id)
            or {
                "worker_id": worker_id,
                "pending": 0,
                "running": 0,
                "queue_depth": 0,
                "avg_dispatch_latency_sec": 0.0,
                "avg_completion_sec": 0.0,
                "completion_rate": 1.0,
            }
        )


class _FakeWorkerTaskStore:
    def __init__(self):
        self.calls = []
        self.rows = []

    async def upsert_task(self, **kwargs):
        self.calls.append(dict(kwargs))
        return dict(kwargs)

    async def list_recent(self, worker_id: str | None = None, limit: int = 20):
        _ = limit
        if not worker_id:
            return list(self.rows)
        return [row for row in self.rows if row.get("worker_id") == worker_id]


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
    assert fake_queue.last_priority == 40


@pytest.mark.asyncio
async def test_manager_dispatch_service_prefers_lower_load_worker(monkeypatch):
    registry = _FakeRegistry()
    registry.rows = [
        {
            "id": "worker-main",
            "name": "Main Worker",
            "status": "ready",
            "backend": "core-agent",
            "capabilities": ["code"],
            "summary": "通用执行",
        },
        {
            "id": "worker-rss",
            "name": "RSS Worker",
            "status": "ready",
            "backend": "core-agent",
            "capabilities": ["rss", "feed"],
            "summary": "专门处理 RSS",
        },
    ]
    monkeypatch.setattr(service_module, "worker_registry", registry)

    fake_queue = _FakeQueue()
    fake_queue.metrics = {
        "worker-main": {
            "worker_id": "worker-main",
            "pending": 5,
            "running": 2,
            "queue_depth": 7,
            "avg_dispatch_latency_sec": 8.0,
            "avg_completion_sec": 20.0,
            "completion_rate": 0.7,
        },
        "worker-rss": {
            "worker_id": "worker-rss",
            "pending": 0,
            "running": 0,
            "queue_depth": 0,
            "avg_dispatch_latency_sec": 1.0,
            "avg_completion_sec": 5.0,
            "completion_rate": 1.0,
        },
    }
    fake_store = _FakeWorkerTaskStore()
    fake_store.rows = [
        {"worker_id": "worker-main", "status": "failed"},
        {"worker_id": "worker-main", "status": "failed"},
        {"worker_id": "worker-main", "status": "done"},
        {"worker_id": "worker-rss", "status": "done"},
        {"worker_id": "worker-rss", "status": "done"},
    ]
    monkeypatch.setattr(service_module, "dispatch_queue", fake_queue)
    monkeypatch.setattr(service_module, "worker_task_store", fake_store)

    result = await service_module.manager_dispatch_service.dispatch_worker(
        instruction="请帮我检查 RSS 更新",
        metadata={},
        source="user_chat",
    )

    assert result["ok"] is True
    assert result["worker_id"] == "worker-rss"
    assert result["priority"] == 60
    assert result["selection_score"] > 0
    assert result["worker_metrics"]["queue_depth"] == 0
