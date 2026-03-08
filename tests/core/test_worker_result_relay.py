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
        self.photos: list[dict] = []

    async def send_message(self, **kwargs):
        self.messages.append(dict(kwargs))
        return {"ok": True}

    async def send_message_draft(self, **kwargs):
        self.drafts.append(dict(kwargs))
        return True

    async def send_document(self, **kwargs):
        self.documents.append(dict(kwargs))
        return {"ok": True}

    async def send_photo(self, **kwargs):
        self.photos.append(dict(kwargs))
        return {"ok": True}


@pytest.fixture(autouse=True)
def _disable_live_delivery_summary(monkeypatch):
    async def fallback_summary(self, *, task, result, text, files):
        return relay_module._fallback_delivery_body(
            task,
            result,
            text=text,
            files=files,
        )

    monkeypatch.setattr(
        WorkerResultRelay,
        "_summarize_delivery_body",
        fallback_summary,
    )


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
async def test_worker_result_relay_extracts_saved_file_marker(monkeypatch, tmp_path):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    image_path = (tmp_path / "catdog.png").resolve()
    image_path.write_bytes(b"png")

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-text-path",
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        metadata={"worker_name": "阿黑"},
    )
    result = {
        "ok": True,
        "payload": {
            "text": (
                "✅ 图片已成功生成！\n"
                f"saved_file={image_path}\n"
                "请查收。"
            ),
        },
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="c-text-path",
        task=task,
        result=result,
    )

    assert delivered is True
    assert fake_adapter.photos
    assert fake_adapter.photos[0]["photo"] == str(image_path)
    assert fake_adapter.messages


@pytest.mark.asyncio
async def test_worker_result_relay_summarizes_raw_search_dump(monkeypatch, tmp_path):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    async def fake_summary(self, *, task, result, text, files):
        _ = (self, task, result, text, files)
        return "无锡滨湖区今天天气多云转阴，约 15/5℃，风力较弱。详细报告见附件。"

    monkeypatch.setattr(
        WorkerResultRelay,
        "_summarize_delivery_body",
        fake_summary,
    )

    report_path = (tmp_path / "search_report.md").resolve()
    report_path.write_text("report", encoding="utf-8")

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-search-summary",
        worker_id="worker-main",
        instruction="查询无锡滨湖区今天天气",
        source="user_chat",
        metadata={"worker_name": "阿黑"},
    )
    result = {
        "ok": True,
        "payload": {
            "text": (
                "🔇🔇🔇【搜索结果摘要】\n\n"
                "## 搜索: 无锡滨湖区天气 今日\n"
                "- [7日（今天） - 天气](https://www.weather.com.cn/weather/101190206.shtml)\n"
                "15 / 5℃\n"
            ),
            "files": [
                {
                    "kind": "document",
                    "path": str(report_path),
                    "filename": "search_report.md",
                }
            ],
        },
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="c-search-summary",
        task=task,
        result=result,
    )

    assert delivered is True
    assert fake_adapter.messages
    final_text = str(fake_adapter.messages[-1]["text"])
    assert "多云转阴" in final_text
    assert "搜索结果摘要" not in final_text
    assert fake_adapter.documents


@pytest.mark.asyncio
async def test_worker_result_relay_fallback_strips_internal_sections(monkeypatch):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-cleanup",
        worker_id="worker-main",
        instruction="生成图片",
        source="user_chat",
        metadata={"worker_name": "阿黑"},
    )
    result = {
        "ok": True,
        "payload": {
            "text": (
                "工具选择策略\n"
                "- 任务: 生成图片\n\n"
                "执行日志\n"
                "- [generate_image] 调用脚本成功\n\n"
                "最终结果\n"
                "✅ 图片已生成。\n"
                "比例: 1:1\n"
                "图片路径: /app/data/users/u1/skills/generate_image/outputs/demo.png\n"
            )
        },
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="c-cleanup",
        task=task,
        result=result,
    )

    assert delivered is True
    assert fake_adapter.messages
    final_text = str(fake_adapter.messages[-1]["text"])
    assert "工具选择策略" not in final_text
    assert "执行日志" not in final_text
    assert "图片路径" not in final_text
    assert "图片已生成" in final_text


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


@pytest.mark.asyncio
async def test_worker_result_relay_progress_hides_verbose_tool_output(monkeypatch):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    task = TaskEnvelope(
        task_id="tsk-progress-verbose",
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        status="running",
        metadata={
            "platform": "telegram",
            "chat_id": "chat-progress-verbose",
            "worker_name": "阿黑",
            "progress_events": [
                {
                    "seq": 1,
                    "event": "tool_call_finished",
                    "turn": 2,
                    "recent_steps": [
                        {
                            "name": "bash",
                            "status": "done",
                            "summary": "🌦️ 正在查询 无锡 的天气预报...\n✅ 中国 江苏 无锡 的天气预报如下（备用天气源 Open-Meteo）：\n当前：8.2°C，局部多云",
                        }
                    ],
                }
            ],
        },
    )

    class _FakeDispatchQueue:
        async def list_running(self, *, limit: int = 20):
            _ = limit
            return [task]

        async def ack_progress_events(self, task_id: str, *, upto_seq: int, last_event: str):
            _ = (task_id, upto_seq, last_event)
            return True

        async def list_undelivered(self, *, limit: int = 20):
            _ = limit
            return []

    monkeypatch.setattr(relay_module, "dispatch_queue", _FakeDispatchQueue())

    relay = WorkerResultRelay()
    await relay.process_once()

    assert fake_adapter.drafts
    text = str(fake_adapter.drafts[-1]["text"])
    assert "已获得结果，正在整理最终回复。" in text
    assert "当前：8.2°C" not in text


@pytest.mark.asyncio
async def test_worker_result_relay_auto_repairs_failed_learned_skill(monkeypatch):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    monkeypatch.setattr(
        "core.skill_loader.skill_loader.get_skills_summary",
        lambda: [
            {
                "name": "union-search-skill",
                "description": "external search",
                "source": "learned",
            }
        ],
    )
    monkeypatch.setattr(
        "core.skill_loader.skill_loader.get_skill",
        lambda _name: {
            "name": "union-search-skill",
            "source": "learned",
            "skill_dir": "/app/skills/learned/union_search_skill",
        },
    )

    async def fake_software_delivery(**kwargs):
        assert kwargs.get("action") == "skill_modify"
        assert kwargs.get("skill_name") == "union-search-skill"
        assert kwargs.get("source") == "worker_skill_auto_repair"
        return {
            "ok": True,
            "task_id": "dev-auto-repair-1",
            "status": "queued",
        }

    monkeypatch.setattr(
        "manager.dev.service.manager_dev_service.software_delivery",
        fake_software_delivery,
    )
    async def fake_list_recent(limit=40):
        _ = limit
        return []

    monkeypatch.setattr(
        "manager.dev.task_store.dev_task_store.list_recent",
        fake_list_recent,
    )

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-auto-repair",
        worker_id="worker-main",
        instruction="请使用 union-search-skill 搜索 Bilibili 最新热门视频",
        source="user_chat",
        metadata={
            "worker_name": "阿黑",
            "user_id": "u-1",
        },
    )
    result = {
        "ok": False,
        "summary": "python: can't open file 'scripts/execute.py'",
        "error": "python: can't open file 'scripts/execute.py'",
        "payload": {"text": "python: can't open file 'scripts/execute.py'"},
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="chat-1",
        task=task,
        result=result,
    )

    assert delivered is True
    assert fake_adapter.messages
    final_text = str(fake_adapter.messages[-1].get("text") or "")
    assert "dev-auto-repair-1" in final_text
    assert "自动发起技能修复任务" in final_text
