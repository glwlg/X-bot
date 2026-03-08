from pathlib import Path

import pytest

from core.runtime_callbacks import get_runtime_callback
from shared.contracts.dispatch import TaskEnvelope
import shared.queue.dispatch_queue as dispatch_queue_module
from worker.programs import core_agent_program as program_module


@pytest.mark.asyncio
async def test_run_core_agent_preserves_terminal_payload_files(monkeypatch, tmp_path):
    image_path = (tmp_path / "ikaros.png").resolve()
    image_path.write_bytes(b"png")

    class _FakeDispatchQueue:
        def __init__(self):
            self.progress_events: list[dict] = []

        async def update_progress(self, task_id, snapshot):
            self.progress_events.append(
                {
                    "task_id": str(task_id),
                    "snapshot": dict(snapshot or {}),
                }
            )

    async def _fake_handle_message(ctx, history):
        del history
        callback = get_runtime_callback(ctx, "worker_progress_callback")
        assert callable(callback)
        await callback(
            {
                "event": "tool_call_finished",
                "turn": 2,
                "name": "bash",
                "ok": True,
                "summary": "✅ 图片已生成。",
                "terminal": True,
                "task_outcome": "done",
                "terminal_text": "✅ 图片已生成。\n📏 比例: 1:1",
                "terminal_payload": {
                    "text": "✅ 图片已生成。\n📏 比例: 1:1",
                    "files": [
                        {
                            "kind": "photo",
                            "path": str(image_path),
                            "filename": "ikaros.png",
                        }
                    ],
                },
            }
        )
        yield "✅ 图片已生成。\n📏 比例: 1:1"

    fake_queue = _FakeDispatchQueue()
    monkeypatch.setattr(dispatch_queue_module, "dispatch_queue", fake_queue)
    monkeypatch.setattr(
        program_module.agent_orchestrator,
        "handle_message",
        _fake_handle_message,
    )

    task = TaskEnvelope(
        task_id="tsk-files",
        worker_id="worker-main",
        instruction="请画一张伊卡洛斯",
        source="manager_dispatch",
        metadata={"user_id": "u-1"},
    )

    result = await program_module.run_core_agent(task, {"worker_id": "worker-main"})

    assert result.ok is True
    assert result.payload["files"][0]["path"] == str(image_path)
    assert "图片已生成" in result.payload["text"]
    assert fake_queue.progress_events
