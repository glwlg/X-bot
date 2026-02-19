from pathlib import Path

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
