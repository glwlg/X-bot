from pathlib import Path

import pytest

import manager.relay.result_relay as relay_module
from manager.relay.result_relay import WorkerResultRelay
from shared.contracts.dispatch import TaskEnvelope


class _FakeAdapter:
    def __init__(self):
        self.messages: list[dict] = []
        self.documents: list[dict] = []

    async def send_message(self, **kwargs):
        self.messages.append(dict(kwargs))
        return {"ok": True}

    async def send_document(self, **kwargs):
        self.documents.append(dict(kwargs))
        return {"ok": True}


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

    fake_queue = _FakeDispatchQueue()
    monkeypatch.setattr(relay_module, "dispatch_queue", fake_queue)

    relay = WorkerResultRelay()
    await relay.process_once()

    assert fake_queue.marked == ["tsk-process-1"]
    assert fake_adapter.messages
