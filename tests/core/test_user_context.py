from pathlib import Path
from types import SimpleNamespace

import pytest

from core.task_inbox import task_inbox
from core.heartbeat_store import heartbeat_store
from user_context import SESSION_ID_KEY, get_or_create_session_id, get_user_context


def _reset_task_inbox(tmp_path: Path) -> None:
    root = (tmp_path / "task_inbox").resolve()
    tasks_root = (root / "tasks").resolve()
    archive_root = (root / "archive").resolve()
    events_path = (root / "events.jsonl").resolve()
    tasks_root.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)
    task_inbox.persist = True
    task_inbox.root = root
    task_inbox.tasks_root = tasks_root
    task_inbox.archive_root = archive_root
    task_inbox.events_path = events_path
    task_inbox._loaded = False
    task_inbox._tasks = {}


def _reset_heartbeat_store(tmp_path: Path) -> None:
    root = (tmp_path / "runtime_tasks").resolve()
    root.mkdir(parents=True, exist_ok=True)
    heartbeat_store.root = root
    heartbeat_store._locks.clear()


@pytest.mark.asyncio
async def test_get_user_context_reconciles_sparse_session_from_task_inbox(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reset_task_inbox(tmp_path)
    _reset_heartbeat_store(tmp_path)

    task = await task_inbox.submit(
        source="user_chat",
        goal="好了吗",
        user_id="u-reconcile",
        payload={"session_id": "sess-reconcile-1"},
        metadata={"session_id": "sess-reconcile-1"},
    )
    await task_inbox.complete(
        task.task_id,
        result={"summary": "服务已经恢复正常"},
        final_output="服务已经恢复正常",
    )

    ctx = SimpleNamespace(user_data={SESSION_ID_KEY: "sess-reconcile-1"})

    history = await get_user_context(
        ctx,
        "u-reconcile",
        include_hidden_system=False,
        auto_compact=False,
    )

    assert history == [
        {"role": "user", "parts": [{"text": "好了吗"}]},
        {"role": "model", "parts": [{"text": "服务已经恢复正常"}]},
    ]


@pytest.mark.asyncio
async def test_get_or_create_session_id_prefers_bound_delivery_session(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reset_task_inbox(tmp_path)
    _reset_heartbeat_store(tmp_path)

    await heartbeat_store.set_delivery_target(
        "u-bound",
        "telegram",
        "chat-1",
        session_id="sess-bound-1",
    )
    ctx = SimpleNamespace(user_data={})

    session_id = await get_or_create_session_id(ctx, "u-bound")

    assert session_id == "sess-bound-1"
    assert ctx.user_data[SESSION_ID_KEY] == "sess-bound-1"
