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
        _ = metadata
        return type(
            "_Queued",
            (),
            {
                "task_id": "tsk-queued-1",
                "worker_id": worker_id,
                "instruction": instruction,
            },
        )()


@pytest.mark.asyncio
async def test_manager_dispatch_service_dispatches_async_task(monkeypatch):
    monkeypatch.setattr(service_module, "worker_registry", _FakeRegistry())
    monkeypatch.setattr(service_module, "dispatch_queue", _FakeQueue())

    result = await service_module.manager_dispatch_service.dispatch_worker(
        instruction="请处理这个任务",
        metadata={"session_id": "session-1"},
    )

    assert result["ok"] is True
    assert result["task_id"] == "tsk-queued-1"
    assert result["terminal"] is False
    assert result["task_outcome"] == "partial"
