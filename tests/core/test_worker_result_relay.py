from pathlib import Path

import pytest

import manager.relay.result_relay as relay_module
from manager.relay.result_relay import WorkerResultRelay
from shared.contracts.dispatch import TaskEnvelope, TaskResult
from shared.queue.dispatch_queue import DispatchQueue


class _FakeAdapter:
    def __init__(self):
        self.messages: list[dict] = []
        self.drafts: list[dict] = []
        self.documents: list[dict] = []

    async def send_message(self, **kwargs):
        self.messages.append(dict(kwargs))
        return {"ok": True}

    async def send_message_draft(self, **kwargs):
        self.drafts.append(dict(kwargs))
        return True

    async def send_document(self, **kwargs):
        self.documents.append(dict(kwargs))
        return {"ok": True}


async def _create_finished_task(
    queue: DispatchQueue,
    *,
    metadata: dict | None = None,
    ok: bool = True,
) -> TaskEnvelope:
    submitted = await queue.submit_task(
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        metadata=dict(metadata or {}),
    )
    claimed = await queue.claim_next(worker_id="worker-main", claimer="worker-daemon")
    assert claimed is not None
    result = TaskResult(
        task_id=submitted.task_id,
        worker_id="worker-main",
        ok=ok,
        summary="done" if ok else "failed",
        error="" if ok else "failed",
        payload={"text": "done" if ok else "failed"},
    )
    finished = await queue.finish_task(task_id=submitted.task_id, result=result)
    assert finished is not None
    return finished


@pytest.mark.asyncio
async def test_worker_result_relay_delivers_files_and_text(monkeypatch, tmp_path):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    image_path = (tmp_path / "dog.png").resolve()
    image_path.write_bytes(b"png-bytes")

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-1",
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        metadata={"worker_name": "阿黑"},
    )
    result = {
        "ok": True,
        "payload": {
            "text": "图片已完成",
            "files": [
                {
                    "kind": "document",
                    "path": str(image_path),
                    "filename": "dog.png",
                }
            ],
        },
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="c-1",
        task=task,
        result=result,
    )

    assert delivered is True
    assert fake_adapter.documents
    assert fake_adapter.documents[0]["filename"] == "dog.png"
    assert Path(fake_adapter.documents[0]["document"]).exists()
    assert fake_adapter.messages


@pytest.mark.asyncio
async def test_worker_result_relay_deduplicates_duplicate_files(monkeypatch, tmp_path):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    report_path = (tmp_path / "search_report.html").resolve()
    report_path.write_text("report", encoding="utf-8")

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-dup",
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        metadata={"worker_name": "阿黑"},
    )
    result = {
        "ok": True,
        "payload": {
            "text": "done",
            "files": [
                {
                    "kind": "document",
                    "path": str(report_path),
                    "filename": "search_report.html",
                },
                {
                    "kind": "document",
                    "path": str(report_path),
                    "filename": "search_report.html",
                },
            ],
        },
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="c-dup",
        task=task,
        result=result,
    )

    assert delivered is True
    assert len(fake_adapter.documents) == 1
    assert fake_adapter.documents[0]["filename"] == "search_report.html"


@pytest.mark.asyncio
async def test_worker_result_relay_process_once_marks_delivered(monkeypatch):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    task = TaskEnvelope(
        task_id="tsk-process-1",
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        status="done",
        metadata={"platform": "telegram", "chat_id": "chat-1", "worker_name": "阿黑"},
    )

    class _FakeDispatchQueue:
        def __init__(self):
            self.marked: list[str] = []

        async def list_undelivered(self, *, limit: int = 20):
            _ = limit
            return [task]

        async def latest_result(self, task_id: str):
            _ = task_id

            class _Result:
                def to_dict(self_inner):
                    return {"ok": True, "summary": "done", "payload": {"text": "done"}}

            return _Result()

        async def mark_delivered(self, task_id: str):
            self.marked.append(str(task_id))
            return True

        async def clear_relay_retry(self, task_id: str):
            _ = task_id
            return True

    fake_queue = _FakeDispatchQueue()
    monkeypatch.setattr(relay_module, "dispatch_queue", fake_queue)

    relay = WorkerResultRelay()
    await relay.process_once()

    assert fake_queue.marked == ["tsk-process-1"]
    assert fake_adapter.messages


@pytest.mark.asyncio
async def test_worker_result_relay_missing_target_schedules_retry(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    queue = DispatchQueue()
    finished = await _create_finished_task(
        queue,
        metadata={"worker_name": "阿黑"},
    )

    monkeypatch.setattr(relay_module, "dispatch_queue", queue)
    relay = WorkerResultRelay()
    await relay.process_once()

    task = await queue.get_task(finished.task_id)
    assert task is not None
    assert not str(task.delivered_at or "").strip()
    relay_meta = dict((task.metadata or {}).get("_relay") or {})
    assert int(relay_meta.get("attempts") or 0) == 1
    assert str(relay_meta.get("state") or "") == "retrying"
    assert str(relay_meta.get("next_retry_at") or "").strip()


@pytest.mark.asyncio
async def test_worker_result_relay_moves_to_dead_letter_after_max_retries(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)
    monkeypatch.setenv("WORKER_RESULT_RELAY_MAX_RETRIES", "2")
    monkeypatch.setenv("WORKER_RESULT_RELAY_RETRY_BASE_SEC", "0")
    monkeypatch.setenv("WORKER_RESULT_RELAY_RETRY_MAX_SEC", "0")

    queue = DispatchQueue()
    finished = await _create_finished_task(
        queue,
        metadata={"worker_name": "阿黑"},
    )
    monkeypatch.setattr(relay_module, "dispatch_queue", queue)

    relay = WorkerResultRelay()
    await relay.process_once()
    await relay.process_once()

    task = await queue.get_task(finished.task_id)
    assert task is not None
    assert not str(task.delivered_at or "").strip()
    relay_meta = dict((task.metadata or {}).get("_relay") or {})
    assert int(relay_meta.get("attempts") or 0) == 2
    assert str(relay_meta.get("state") or "") == "dead_letter"
    assert str(relay_meta.get("dead_letter_at") or "").strip()

    # dead-letter tasks should stop retrying further attempts.
    await relay.process_once()
    task_again = await queue.get_task(finished.task_id)
    assert task_again is not None
    relay_meta_again = dict((task_again.metadata or {}).get("_relay") or {})
    assert int(relay_meta_again.get("attempts") or 0) == 2


@pytest.mark.asyncio
async def test_worker_result_relay_delivers_progress_updates(monkeypatch):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    task = TaskEnvelope(
        task_id="tsk-progress-1",
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        status="running",
        metadata={
            "platform": "telegram",
            "chat_id": "chat-progress",
            "worker_name": "阿黑",
            "progress_events": [
                {
                    "seq": 1,
                    "event": "tool_call_started",
                    "turn": 1,
                    "running_tool": "load_skill",
                    "recent_steps": [
                        {"name": "load_skill", "status": "running", "summary": ""}
                    ],
                },
                {
                    "seq": 2,
                    "event": "tool_call_finished",
                    "turn": 1,
                    "recent_steps": [
                        {
                            "name": "load_skill",
                            "status": "done",
                            "summary": "loaded daily_query",
                        }
                    ],
                },
            ],
        },
    )

    class _FakeDispatchQueue:
        def __init__(self):
            self.acked: list[tuple[str, int, str]] = []

        async def list_running(self, *, limit: int = 20):
            _ = limit
            return [task]

        async def ack_progress_events(self, task_id: str, *, upto_seq: int, last_event: str):
            self.acked.append((task_id, upto_seq, last_event))
            return True

        async def list_undelivered(self, *, limit: int = 20):
            _ = limit
            return []

    fake_queue = _FakeDispatchQueue()
    monkeypatch.setattr(relay_module, "dispatch_queue", fake_queue)

    relay = WorkerResultRelay()
    await relay.process_once()

    assert len(fake_adapter.drafts) == 2
    assert "开始执行" in fake_adapter.drafts[0]["text"]
    assert "执行完成" in fake_adapter.drafts[1]["text"]
    assert fake_adapter.drafts[0]["draft_id"] == fake_adapter.drafts[1]["draft_id"]
    assert fake_queue.acked == [("tsk-progress-1", 2, "tool_call_finished")]
