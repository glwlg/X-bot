import importlib

import pytest

dispatch_tools_module = importlib.import_module("core.tools.dispatch_tools")


class _FakeManagerDispatchService:
    async def list_workers(self):
        return {
            "ok": True,
            "workers": [{"id": "worker-main", "name": "Main Worker"}],
            "summary": "1 worker(s) available",
        }

    async def dispatch_worker(
        self,
        *,
        instruction: str,
        worker_id: str = "",
        backend: str = "",
        source: str = "manager_dispatch",
        metadata: dict | None = None,
    ):
        _ = worker_id
        _ = backend
        _ = source
        _ = metadata
        return {
            "ok": True,
            "worker_id": "worker-main",
            "worker_name": "Main Worker",
            "task_id": "tsk-1",
            "summary": f"queued:{instruction}",
            "text": "worker dispatch accepted",
            "terminal": False,
            "task_outcome": "partial",
            "async_dispatch": True,
        }


class _FakeTask:
    def __init__(self, payload: dict):
        self._payload = dict(payload)

    def to_dict(self):
        return dict(self._payload)


class _FakeDispatchQueue:
    async def list_tasks(
        self, *, worker_id: str = "", status: str = "", limit: int = 50
    ):
        _ = status
        _ = limit
        return [
            _FakeTask(
                {
                    "task_id": "tsk-1",
                    "worker_id": worker_id or "worker-main",
                    "status": "done",
                }
            )
        ]

    async def delivery_health(
        self, *, worker_id: str = "", dead_letter_limit: int = 20
    ):
        _ = dead_letter_limit
        return {
            "worker_id": worker_id,
            "undelivered": 2,
            "retrying": 1,
            "dead_letter": 1,
            "result_persist_error": 0,
            "recent_dead_letters": [
                {
                    "task_id": "tsk-dead-1",
                    "worker_id": worker_id or "worker-main",
                    "status": "failed",
                }
            ],
        }

    async def requeue_dead_letter(
        self,
        *,
        task_id: str,
        reason: str = "manual_requeue",
    ):
        if str(task_id or "").strip() == "tsk-dead-1":
            return {
                "ok": True,
                "task_id": "tsk-dead-1",
                "retried": True,
                "summary": f"requeued:{reason}",
            }
        return {
            "ok": False,
            "task_id": str(task_id or ""),
            "retried": False,
            "summary": "task not found",
        }


@pytest.mark.asyncio
async def test_list_workers_delegates_to_manager_service(monkeypatch):
    monkeypatch.setattr(
        dispatch_tools_module,
        "manager_dispatch_service",
        _FakeManagerDispatchService(),
    )

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.list_workers()

    assert result["ok"] is True
    assert result["workers"][0]["id"] == "worker-main"


@pytest.mark.asyncio
async def test_dispatch_worker_delegates_to_manager_service(monkeypatch):
    monkeypatch.setattr(
        dispatch_tools_module,
        "manager_dispatch_service",
        _FakeManagerDispatchService(),
    )

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.dispatch_worker(
        instruction="10秒后提醒我喝水",
        metadata={"session_id": "session-1"},
    )

    assert result["ok"] is True
    assert result["task_id"] == "tsk-1"
    assert result["terminal"] is False
    assert result["task_outcome"] == "partial"


@pytest.mark.asyncio
async def test_worker_status_reads_from_shared_dispatch_queue(monkeypatch):
    monkeypatch.setattr(
        dispatch_tools_module,
        "dispatch_queue",
        _FakeDispatchQueue(),
    )

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.worker_status(worker_id="worker-main", limit=5)

    assert result["ok"] is True
    assert result["tasks"]
    assert result["tasks"][0]["worker_id"] == "worker-main"
    assert int(result["delivery_health"]["dead_letter"]) == 1
    assert "dead_letter=1" in str(result["summary"] or "")


@pytest.mark.asyncio
async def test_retry_dead_letter_delegates_to_dispatch_queue(monkeypatch):
    monkeypatch.setattr(
        dispatch_tools_module,
        "dispatch_queue",
        _FakeDispatchQueue(),
    )

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.retry_dead_letter(
        task_id="tsk-dead-1",
        reason="manual_operator_retry",
    )

    assert result["ok"] is True
    assert result["retried"] is True
    assert result["task_id"] == "tsk-dead-1"


@pytest.mark.asyncio
async def test_retry_dead_letter_requires_task_id(monkeypatch):
    monkeypatch.setattr(
        dispatch_tools_module,
        "dispatch_queue",
        _FakeDispatchQueue(),
    )

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.retry_dead_letter(task_id="   ")

    assert result["ok"] is False
    assert result["retried"] is False
