from datetime import datetime, timedelta
from pathlib import Path

import pytest

import manager.relay.result_relay as relay_module
from manager.relay.delivery_store import delivery_store
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


def _reset_delivery_store(tmp_path: Path) -> None:
    root = (tmp_path / "system" / "delivery").resolve()
    jobs_root = (root / "jobs").resolve()
    events_path = (root / "events.jsonl").resolve()
    jobs_root.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    delivery_store.root = root
    delivery_store.jobs_root = jobs_root
    delivery_store.events_path = events_path
    delivery_store._loaded = False
    delivery_store._jobs = {}


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


@pytest.fixture(autouse=True)
def _isolated_delivery_store(tmp_path):
    _reset_delivery_store(tmp_path)
    return tmp_path


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
async def test_worker_result_relay_prefers_full_text_for_user_facing_final_output(
    monkeypatch,
):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-final-full-text",
        worker_id="worker-main",
        instruction="介绍郭子仪的详细生平",
        source="user_chat",
        metadata={"worker_name": "阿黑"},
    )
    full_text = (
        "郭子仪（697年—781年），是唐代中后期最重要的军事统帅与重臣之一。\n\n"
        "## 一、出身与早年\n\n"
        "郭子仪早年以武举入仕，长期在边镇历练，逐步积累了稳定的军政经验。\n\n"
        "## 二、安史之乱中的作用\n\n"
        "安史之乱爆发后，郭子仪与李光弼等将领并肩作战，先后参与收复长安、洛阳，"
        "成为支撑肃宗政权的重要支柱。\n\n"
        "## 三、历史地位\n\n"
        "他不仅能打仗，也善于在乱局中重建秩序，因此后世常把他视为“再造大唐”的功臣。\n\n"
        "## 简短总结\n\n"
        "郭子仪最难得之处，不只是战功卓著，更在于他多次把唐朝从崩溃边缘拉了回来。"
    )
    result = {
        "ok": True,
        "summary": "郭子仪（697年—781年），是唐代中后期最重要的军事统帅与重臣之一。\n\n## 一、出身与早年\n\n郭子仪早年以武举入仕，长",
        "payload": {
            "text": full_text,
            "delivery_mode": "full_text",
            "user_facing_output": True,
        },
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="c-final-full-text",
        task=task,
        result=result,
    )

    assert delivered is True
    assert fake_adapter.messages
    final_text = str(fake_adapter.messages[-1]["text"])
    assert final_text.startswith("✅ 阿黑 已完成任务")
    assert "安史之乱中的作用" in final_text
    assert "把唐朝从崩溃边缘拉了回来" in final_text
    assert final_text.endswith(full_text)


@pytest.mark.asyncio
async def test_worker_result_relay_delivers_waiting_user_summary_for_staged_failure(
    monkeypatch,
):
    fake_adapter = _FakeAdapter()
    monkeypatch.setattr(
        relay_module.adapter_manager,
        "get_adapter",
        lambda _platform: fake_adapter,
    )

    class _FakeClosureService:
        async def resolve_attempt(self, *, task, result, platform, chat_id):
            _ = (task, result, platform, chat_id)
            return {
                "kind": "waiting_user",
                "text": (
                    "⏸ 任务暂时卡住了，但我还没有结束它。\n\n"
                    "建议下一步：\n- 回复“继续”\n- 或直接补充约束"
                ),
                "ui": {
                    "actions": [
                        [
                            {"text": "继续执行", "callback_data": "task_continue"},
                            {"text": "停止任务", "callback_data": "task_stop"},
                        ]
                    ]
                },
                "files": [],
                "auto_repair_allowed": False,
            }

    monkeypatch.setattr(relay_module, "manager_closure_service", _FakeClosureService())

    relay = WorkerResultRelay()
    task = TaskEnvelope(
        task_id="tsk-stage-blocked",
        worker_id="worker-main",
        instruction="do",
        source="user_chat",
        metadata={
            "worker_name": "阿黑",
            "staged_session": True,
            "task_inbox_id": "session-1",
            "session_task_id": "session-1",
        },
    )
    result = {
        "ok": False,
        "summary": "failed",
        "error": "failed",
        "payload": {"text": "failed"},
    }

    delivered = await relay._deliver_task(
        platform="telegram",
        chat_id="chat-stage-blocked",
        task=task,
        result=result,
    )

    assert delivered is True
    assert fake_adapter.messages
    final_text = str(fake_adapter.messages[-1]["text"])
    assert "任务暂时卡住了" in final_text
    assert "任务执行失败" not in final_text


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

        async def get_task(self, task_id: str):
            _ = task_id
            return task

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


@pytest.mark.asyncio
async def test_delivery_jobs_prioritize_interactive_before_background():
    relay = WorkerResultRelay()
    interactive = TaskEnvelope(
        task_id="tsk-interactive",
        worker_id="worker-main",
        instruction="帮我总结仓库结构",
        source="user_chat",
        status="done",
        metadata={"user_id": "u-1"},
    )
    background = TaskEnvelope(
        task_id="tsk-background",
        worker_id="worker-main",
        instruction="heartbeat run",
        source="heartbeat",
        status="done",
        metadata={"user_id": "u-1", "session_task_id": "hb-1"},
    )

    await delivery_store.ensure_job(
        task=background,
        priority=relay._delivery_priority(background),
        body_mode=relay._delivery_body_mode(background),
    )
    await delivery_store.ensure_job(
        task=interactive,
        priority=relay._delivery_priority(interactive),
        body_mode=relay._delivery_body_mode(interactive),
    )

    ready = await delivery_store.list_ready(limit=10)

    assert [job.task_id for job in ready][:2] == ["tsk-interactive", "tsk-background"]
    assert ready[0].priority == "interactive"
    assert ready[1].priority == "background"


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

    job = await delivery_store.get(finished.task_id)
    assert job is not None
    assert job.status == "retrying"
    assert job.attempts == 1


@pytest.mark.asyncio
async def test_worker_result_relay_suppresses_startup_replay_for_old_dead_letter(
    monkeypatch,
):
    class _FakeDispatchQueue:
        def __init__(self):
            self.marked: list[str] = []

        async def list_undelivered(self, *, limit: int = 20):
            _ = limit
            return [
                TaskEnvelope(
                    task_id="tsk-old-dead",
                    worker_id="worker-main",
                    instruction="legacy failed task",
                    source="user_chat",
                    status="failed",
                    metadata={"_relay": {"state": "dead_letter"}},
                    created_at=(
                        datetime.now().astimezone() - timedelta(days=2)
                    ).isoformat(timespec="seconds"),
                    updated_at=(
                        datetime.now().astimezone() - timedelta(days=2)
                    ).isoformat(timespec="seconds"),
                    ended_at=(
                        datetime.now().astimezone() - timedelta(days=2)
                    ).isoformat(timespec="seconds"),
                )
            ]

        async def mark_delivered(self, task_id: str):
            self.marked.append(str(task_id))
            return True

    fake_queue = _FakeDispatchQueue()
    monkeypatch.setattr(relay_module, "dispatch_queue", fake_queue)

    relay = WorkerResultRelay()
    relay.started_at_ts = datetime.now().astimezone().timestamp()
    await relay._ensure_delivery_jobs()

    job = await delivery_store.get("tsk-old-dead")
    assert job is not None
    assert job.status == "suppressed"
    assert fake_queue.marked == ["tsk-old-dead"]


@pytest.mark.asyncio
async def test_worker_result_relay_suppresses_stale_undelivered_on_startup(
    monkeypatch,
):
    old_ts = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(
        timespec="seconds"
    )

    class _FakeDispatchQueue:
        def __init__(self):
            self.marked: list[str] = []

        async def list_undelivered(self, *, limit: int = 20):
            _ = limit
            return [
                TaskEnvelope(
                    task_id="tsk-old-undelivered",
                    worker_id="worker-main",
                    instruction="old result",
                    source="user_chat",
                    status="done",
                    metadata={},
                    created_at=old_ts,
                    updated_at=old_ts,
                    ended_at=old_ts,
                )
            ]

        async def mark_delivered(self, task_id: str):
            self.marked.append(str(task_id))
            return True

    fake_queue = _FakeDispatchQueue()
    monkeypatch.setattr(relay_module, "dispatch_queue", fake_queue)

    relay = WorkerResultRelay()
    relay.replay_max_age_sec = 300
    relay.started_at_ts = datetime.now().astimezone().timestamp()
    await relay._ensure_delivery_jobs()

    job = await delivery_store.get("tsk-old-undelivered")
    assert job is not None
    assert job.status == "suppressed"
    assert fake_queue.marked == ["tsk-old-undelivered"]
    assert str(job.next_retry_at or "").strip() == ""


@pytest.mark.asyncio
async def test_worker_result_relay_suppresses_existing_retry_job_for_old_task(
    monkeypatch,
):
    old_ts = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(
        timespec="seconds"
    )

    class _FakeDispatchQueue:
        def __init__(self):
            self.marked: list[str] = []

        async def list_undelivered(self, *, limit: int = 20):
            _ = limit
            return [
                TaskEnvelope(
                    task_id="tsk-old-retrying",
                    worker_id="worker-main",
                    instruction="old result",
                    source="user_chat",
                    status="done",
                    metadata={},
                    created_at=old_ts,
                    updated_at=old_ts,
                    ended_at=old_ts,
                )
            ]

        async def mark_delivered(self, task_id: str):
            self.marked.append(str(task_id))
            return True

    fake_queue = _FakeDispatchQueue()
    monkeypatch.setattr(relay_module, "dispatch_queue", fake_queue)

    relay = WorkerResultRelay()
    relay.replay_max_age_sec = 300
    relay.started_at_ts = datetime.now().astimezone().timestamp()
    await delivery_store.ensure_job(
        task=TaskEnvelope(
            task_id="tsk-old-retrying",
            worker_id="worker-main",
            instruction="old result",
            source="user_chat",
            status="done",
            metadata={},
            created_at=old_ts,
            updated_at=old_ts,
            ended_at=old_ts,
        ),
    )
    await delivery_store.schedule_retry(
        task_id="tsk-old-retrying",
        reason="delivery_failed",
        retry_after_sec=1,
        max_retries=6,
    )

    await relay._ensure_delivery_jobs()

    job = await delivery_store.get("tsk-old-retrying")
    assert job is not None
    assert job.status == "suppressed"
    assert fake_queue.marked == ["tsk-old-retrying"]


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

    job = await delivery_store.get(finished.task_id)
    assert job is not None
    assert job.status == "dead_letter"
    assert job.attempts == 2
    assert str(job.updated_at or "").strip()

    # dead-letter tasks should stop retrying further attempts.
    await relay.process_once()
    job_again = await delivery_store.get(finished.task_id)
    assert job_again is not None
    assert job_again.attempts == 2


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
