import asyncio
import json

import pytest

from core.worker_store import WorkerRegistry, WorkerTaskStore


def _build_registry(tmp_path) -> WorkerRegistry:
    registry = WorkerRegistry()
    registry.root = (tmp_path / "userland" / "workers").resolve()
    registry.root.mkdir(parents=True, exist_ok=True)
    registry.meta_path = (tmp_path / "WORKERS.json").resolve()
    registry.meta_path.parent.mkdir(parents=True, exist_ok=True)
    registry._lock = asyncio.Lock()
    return registry


@pytest.mark.asyncio
async def test_worker_registry_prefers_soul_name_when_stored_name_is_default(tmp_path):
    registry = _build_registry(tmp_path)
    workspace = (registry.root / "worker-main").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "SOUL.MD").write_text(
        "# Worker SOUL\n- Name: Atlas\n",
        encoding="utf-8",
    )

    payload = {
        "version": 1,
        "updated_at": "2026-02-16T00:00:00+00:00",
        "workers": {
            "worker-main": {
                "id": "worker-main",
                "name": "Main Worker",
                "backend": "core-agent",
                "status": "ready",
                "capabilities": [],
                "workspace_root": str(workspace),
                "credentials_root": str((tmp_path / "credentials").resolve()),
                "created_at": "2026-02-16T00:00:00+00:00",
                "updated_at": "2026-02-16T00:00:00+00:00",
                "last_task_id": "",
                "last_error": "",
                "auth": {},
            }
        },
    }
    registry.meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    workers = await registry.list_workers()

    assert workers
    assert workers[0]["id"] == "worker-main"
    assert workers[0]["name"] == "Atlas"


@pytest.mark.asyncio
async def test_worker_registry_falls_back_workspace_root_when_configured_path_missing(
    tmp_path,
):
    registry = _build_registry(tmp_path)
    workspace = (registry.root / "worker-main").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "SOUL.MD").write_text(
        "# Worker SOUL\n- Name: 阿黑\n",
        encoding="utf-8",
    )

    payload = {
        "version": 1,
        "updated_at": "2026-02-16T00:00:00+00:00",
        "workers": {
            "worker-main": {
                "id": "worker-main",
                "name": "Main Worker",
                "backend": "core-agent",
                "status": "ready",
                "capabilities": [],
                "workspace_root": "/path/not/available/in/runtime",
                "credentials_root": str((tmp_path / "credentials").resolve()),
                "created_at": "2026-02-16T00:00:00+00:00",
                "updated_at": "2026-02-16T00:00:00+00:00",
                "last_task_id": "",
                "last_error": "",
                "auth": {},
            }
        },
    }
    registry.meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    worker = await registry.get_worker("worker-main")

    assert worker is not None
    assert worker["name"] == "阿黑"
    assert worker["workspace_root"] == str(workspace)


@pytest.mark.asyncio
async def test_worker_registry_generates_summary_when_missing(tmp_path):
    registry = _build_registry(tmp_path)

    payload = {
        "version": 1,
        "updated_at": "2026-02-16T00:00:00+00:00",
        "workers": {
            "worker-main": {
                "id": "worker-main",
                "name": "阿黑",
                "backend": "core-agent",
                "status": "ready",
                "capabilities": [],
                "workspace_root": str((registry.root / "worker-main").resolve()),
                "credentials_root": str((tmp_path / "credentials").resolve()),
                "created_at": "2026-02-16T00:00:00+00:00",
                "updated_at": "2026-02-16T00:00:00+00:00",
                "last_task_id": "",
                "last_error": "",
                "auth": {},
            }
        },
    }
    registry.meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    worker = await registry.get_worker("worker-main")

    assert worker is not None
    assert "summary" in worker
    assert "通用执行助手" in str(worker["summary"])


@pytest.mark.asyncio
async def test_worker_task_store_persists_structured_output(tmp_path):
    store = WorkerTaskStore()
    store.path = (tmp_path / "WORKER_TASKS.jsonl").resolve()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store._lock = asyncio.Lock()

    task = await store.create_task(
        worker_id="worker-main",
        source="user_chat",
        instruction="检查 RSS",
        metadata={"trace": "t1"},
    )
    await store.update_task(
        task["task_id"],
        status="done",
        result="ok",
        output={"text": "ok", "ui": {"actions": [[{"text": "刷新"}]]}},
        ended_at="2026-02-16T20:00:00+08:00",
    )

    rows = await store.list_recent_outputs(worker_id="worker-main", limit=5)
    assert rows
    assert rows[-1]["output"]["text"] == "ok"
    assert rows[-1]["output"]["ui"]["actions"][0][0]["text"] == "刷新"
