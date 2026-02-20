from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

import worker_runtime.result_relay as relay_module
from worker_runtime.result_relay import WorkerResultRelay


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
    job = {
        "job_id": "wj-1",
        "worker_id": "worker-main",
        "result": {
            "ok": True,
            "worker_name": "阿黑",
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
        },
    }

    delivered = await relay._deliver_job(platform="telegram", chat_id="c-1", job=job)

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
    job = {
        "job_id": "wj-dup",
        "worker_id": "worker-main",
        "result": {
            "ok": True,
            "worker_name": "阿黑",
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
                    {
                        "kind": "document",
                        "path": str(report_path),
                        "filename": "search_report.html",
                    },
                ],
            },
        },
    }

    delivered = await relay._deliver_job(platform="telegram", chat_id="c-dup", job=job)

    assert delivered is True
    assert len(fake_adapter.documents) == 1
    assert fake_adapter.documents[0]["filename"] == "search_report.html"


@pytest.mark.asyncio
async def test_worker_result_relay_sends_running_progress_notice_once(monkeypatch):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    started_at = (
        (datetime.now(timezone.utc) - timedelta(seconds=90))
        .astimezone()
        .isoformat(timespec="seconds")
    )

    async def _list_running(limit: int = 20):
        _ = limit
        return [
            {
                "job_id": "wj-running-1",
                "worker_id": "worker-main",
                "started_at": started_at,
                "created_at": started_at,
                "metadata": {
                    "platform": "telegram",
                    "chat_id": "chat-progress",
                    "worker_name": "阿黑",
                    "progress": {
                        "done_tools": ["ext_searxng_search"],
                        "running_tool": "ext_web_browser",
                        "summary": "已完成 ext_searxng_search；正在执行 ext_web_browser",
                    },
                },
            }
        ]

    async def _list_undelivered(limit: int = 20):
        _ = limit
        return []

    monkeypatch.setattr(
        relay_module.worker_task_file_store, "list_running", _list_running
    )
    monkeypatch.setattr(
        relay_module.worker_task_file_store,
        "list_undelivered",
        _list_undelivered,
    )

    relay = WorkerResultRelay()
    relay.progress_notice_sec = 1
    relay.progress_repeat_sec = 3600

    await relay.process_once()
    await relay.process_once()

    progress_msgs = [
        row
        for row in fake_adapter.messages
        if "正在处理中" in str(row.get("text") or "")
    ]
    assert len(progress_msgs) == 1
    assert "已完成" in str(progress_msgs[0].get("text") or "")
    assert "正在执行" in str(progress_msgs[0].get("text") or "")


@pytest.mark.asyncio
async def test_worker_result_relay_skips_stale_running_progress(monkeypatch):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    stale_at = (
        (datetime.now(timezone.utc) - timedelta(minutes=12))
        .astimezone()
        .isoformat(timespec="seconds")
    )

    async def _list_running(limit: int = 20):
        _ = limit
        return [
            {
                "job_id": "wj-running-stale",
                "worker_id": "worker-main",
                "started_at": stale_at,
                "updated_at": stale_at,
                "created_at": stale_at,
                "metadata": {
                    "platform": "telegram",
                    "chat_id": "chat-progress",
                    "worker_name": "阿黑",
                    "progress": {
                        "updated_at": stale_at,
                        "done_tools": ["ext_searxng_search"],
                        "running_tool": "ext_deep_research",
                    },
                },
            }
        ]

    async def _list_undelivered(limit: int = 20):
        _ = limit
        return []

    monkeypatch.setattr(
        relay_module.worker_task_file_store, "list_running", _list_running
    )
    monkeypatch.setattr(
        relay_module.worker_task_file_store,
        "list_undelivered",
        _list_undelivered,
    )

    relay = WorkerResultRelay()
    relay.progress_notice_sec = 1
    relay.progress_repeat_sec = 1
    relay.progress_stale_sec = 30

    await relay.process_once()

    assert fake_adapter.messages == []
