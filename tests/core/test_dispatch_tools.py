import importlib

import pytest

dispatch_tools_module = importlib.import_module("core.tools.dispatch_tools")


class _FakeRegistry:
    def __init__(self):
        self._workers = [
            {
                "id": "worker-main",
                "name": "Main Worker",
                "status": "ready",
                "backend": "core-agent",
                "capabilities": ["code", "rss"],
            },
            {
                "id": "worker-stock",
                "name": "Stock Worker",
                "status": "ready",
                "backend": "core-agent",
                "capabilities": ["stock", "finance"],
            },
        ]

    async def list_workers(self):
        return list(self._workers)

    async def get_worker(self, worker_id: str):
        for item in self._workers:
            if item["id"] == worker_id:
                return dict(item)
        return None

    async def ensure_default_worker(self):
        return dict(self._workers[0])


class _FakeRuntime:
    def _select_allowed_backend(
        self,
        *,
        worker_id: str,
        requested_backend: str | None,
        configured_backend: str | None,
    ):
        _ = worker_id
        selected = str(requested_backend or configured_backend or "core-agent")
        return selected, {"ok": True, "backend": selected}

    async def execute_task(self, **kwargs):
        text = f"executed:{kwargs.get('instruction')}"
        ui = {
            "actions": [[{"text": "刷新", "callback_data": "rss_refresh"}]],
        }
        return {
            "ok": True,
            "task_id": "wt-1",
            "backend": kwargs.get("backend") or "core-agent",
            "runtime_mode": "local",
            "summary": "worker done",
            "result": text,
            "text": text,
            "ui": ui,
            "payload": {"text": text, "ui": ui},
            "error": "",
        }


class _FakeTaskStore:
    async def list_recent(self, worker_id: str = "", limit: int = 10, **_kwargs):
        return [
            {
                "task_id": "wt-1",
                "worker_id": worker_id or "worker-main",
                "status": "done",
                "output": {"text": "worker done"},
            }
        ][:limit]


class _FakeJobStore:
    def __init__(self):
        self.submitted: list[dict] = []

    async def submit(self, **kwargs):
        self.submitted.append(dict(kwargs))
        return {
            "job_id": "wj-1",
            **kwargs,
        }


@pytest.mark.asyncio
async def test_dispatch_worker_sync_mode_returns_worker_result(monkeypatch):
    monkeypatch.setenv("WORKER_DISPATCH_MODE", "sync")
    monkeypatch.setattr(dispatch_tools_module, "worker_registry", _FakeRegistry())
    monkeypatch.setattr(dispatch_tools_module, "worker_runtime", _FakeRuntime())
    monkeypatch.setattr(dispatch_tools_module, "worker_task_store", _FakeTaskStore())

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.dispatch_worker(
        instruction="请抓取最新 RSS 并摘要",
        metadata={"session_id": "session-1"},
    )

    assert result["ok"] is True
    assert result["worker_id"] == "worker-main"
    assert result["worker_name"] == "Main Worker"
    assert result["auto_selected"] is True
    assert result["text"] == "executed:请抓取最新 RSS 并摘要"
    assert result["ui"]["actions"][0][0]["text"] == "刷新"


@pytest.mark.asyncio
async def test_worker_status_returns_recent_tasks(monkeypatch):
    monkeypatch.setattr(dispatch_tools_module, "worker_task_store", _FakeTaskStore())
    tools = dispatch_tools_module.DispatchTools()

    result = await tools.worker_status(worker_id="worker-main", limit=5)

    assert result["ok"] is True
    assert result["tasks"]
    assert result["tasks"][0]["worker_id"] == "worker-main"
    assert result["tasks"][0]["output"]["text"] == "worker done"


@pytest.mark.asyncio
async def test_list_workers_reports_effective_backend(monkeypatch):
    fake_registry = _FakeRegistry()
    fake_runtime = _FakeRuntime()
    monkeypatch.setattr(dispatch_tools_module, "worker_registry", fake_registry)
    monkeypatch.setattr(dispatch_tools_module, "worker_runtime", fake_runtime)

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.list_workers()

    assert result["ok"] is True
    assert result["workers"]
    first = result["workers"][0]
    assert first["backend"] == "core-agent"
    assert first["configured_backend"] == "core-agent"


@pytest.mark.asyncio
async def test_dispatch_worker_async_mode_queues_job_and_returns_ack(
    monkeypatch,
):
    fake_job_store = _FakeJobStore()

    monkeypatch.setenv("WORKER_DISPATCH_MODE", "async")
    monkeypatch.setattr(dispatch_tools_module, "worker_registry", _FakeRegistry())
    monkeypatch.setattr(dispatch_tools_module, "worker_runtime", _FakeRuntime())
    monkeypatch.setattr(dispatch_tools_module, "worker_task_store", _FakeTaskStore())
    monkeypatch.setattr(dispatch_tools_module, "worker_job_store", fake_job_store)

    tools = dispatch_tools_module.DispatchTools()
    result = await tools.dispatch_worker(
        instruction="10秒后提醒我喝水",
        metadata={
            "session_id": "session-async-1",
            "platform": "telegram",
            "chat_id": "c-1",
            "user_id": "u-async",
        },
    )

    assert result["ok"] is True
    assert result["terminal"] is True
    assert result["task_outcome"] == "done"
    assert result["async_dispatch"] is True
    assert result["task_id"] == "wj-1"
    assert "自动把结果发给你" in result["text"]

    assert fake_job_store.submitted
    submitted = fake_job_store.submitted[0]
    assert submitted["worker_id"] == "worker-main"
    assert submitted["instruction"] == "10秒后提醒我喝水"
    assert submitted["metadata"]["worker_name"] == "Main Worker"
    assert submitted["metadata"]["selection_reason"]
    assert submitted["metadata"]["session_id"] == "session-async-1"
