from types import SimpleNamespace

import pytest

import core.heartbeat_worker as heartbeat_worker_module
from core.heartbeat_store import heartbeat_store
from core.heartbeat_worker import HeartbeatWorker


@pytest.mark.asyncio
async def test_heartbeat_worker_manual_run_suppresses_ok(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    await heartbeat_store.set_heartbeat_spec(
        "worker_u1",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.set_delivery_target("worker_u1", "discord", "42")

    async def fake_handle_message(ctx, message_history):
        yield "HEARTBEAT_OK"

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type("FakeOrchestrator", (), {"handle_message": fake_handle_message})(),
    )

    sent = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.suppress_ok = True

    result = await worker.run_user_now("worker_u1")
    assert result == "HEARTBEAT_OK"
    assert sent == []

    state = await heartbeat_store.get_state("worker_u1")
    assert state["status"]["heartbeat"]["last_result"] == "HEARTBEAT_OK"


@pytest.mark.asyncio
async def test_heartbeat_worker_manual_run_pushes_non_ok(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    await heartbeat_store.set_heartbeat_spec(
        "worker_u2",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.set_delivery_target("worker_u2", "discord", "99")

    async def fake_handle_message(ctx, message_history):
        yield "请检查收件箱中 1 封紧急邮件。"

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type("FakeOrchestrator", (), {"handle_message": fake_handle_message})(),
    )

    sent = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.suppress_ok = True
    worker.readonly_dispatch = False

    result = await worker.run_user_now("worker_u2")
    assert "紧急邮件" in result
    assert sent and sent[0][0] == "99"
    assert "紧急邮件" in sent[0][1]


@pytest.mark.asyncio
async def test_heartbeat_worker_readonly_action_does_not_dispatch_to_worker(
    monkeypatch, tmp_path
):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    await heartbeat_store.set_heartbeat_spec(
        "worker_u3",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.set_delivery_target("worker_u3", "discord", "100")

    async def fake_handle_message(ctx, message_history):
        yield "检测到需要修复的配置异常。"

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type("FakeOrchestrator", (), {"handle_message": fake_handle_message})(),
    )

    sent = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.mode = "readonly"
    worker.readonly_dispatch = True
    worker.suppress_ok = True

    result = await worker.run_user_now("worker_u3")
    assert "检测到需要修复的配置异常" in result
    assert "heartbeat readonly 模式" not in result
    assert "Core Manager 治理提醒" not in result
    assert sent and sent[0][0] == "100"


@pytest.mark.asyncio
async def test_heartbeat_worker_long_output_keeps_full_links(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    await heartbeat_store.set_heartbeat_spec(
        "worker_u4",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.set_delivery_target("worker_u4", "discord", "101")

    long_text = (
        "RSS 更新如下：\n"
        + ("这是一段较长的更新摘要。" * 120)
        + "\n链接：https://example.com/rss/articles/abcdefghijklmnopqrstuvwxyz1234567890"
    )

    async def fake_handle_message(ctx, message_history):
        yield long_text

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type("FakeOrchestrator", (), {"handle_message": fake_handle_message})(),
    )

    sent = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.suppress_ok = True

    result = await worker.run_user_now("worker_u4")
    assert "...[truncated]" not in result
    assert (
        "https://example.com/rss/articles/abcdefghijklmnopqrstuvwxyz1234567890"
        in result
    )
    pushed = "\n".join([item[1] for item in sent])
    assert (
        "https://example.com/rss/articles/abcdefghijklmnopqrstuvwxyz1234567890"
        in pushed
    )


def test_heartbeat_prompt_preserves_tool_output_rule():
    prompt = HeartbeatWorker._build_heartbeat_task_prompt(
        task_id="hb-1",
        goal="检查 RSS 更新",
        readonly=True,
    )
    assert "完整保留工具原文" in prompt
    assert "不要改写或删减" in prompt
    assert "补充观察" in prompt


@pytest.mark.asyncio
async def test_heartbeat_push_prefers_adapter_send_message_for_telegram(monkeypatch):
    sent = []

    class _BotShouldNotBeUsed:
        async def send_message(self, **kwargs):
            raise AssertionError("bot.send_message should not be called")

    class _FakeAdapter:
        def __init__(self):
            self.bot = _BotShouldNotBeUsed()

        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    ok = await worker._push_to_target(
        "telegram",
        "101",
        "- [查看详情](https://example.com/a/b)",
    )
    assert ok is True
    assert sent
    assert sent[0][0] == "101"
    assert "[查看详情](https://example.com/a/b)" in sent[0][1]


@pytest.mark.asyncio
async def test_heartbeat_push_sends_markdown_attachment_when_too_long(monkeypatch):
    sent_messages = []
    sent_documents = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent_messages.append((chat_id, text, kwargs))
            return SimpleNamespace(id="sent")

        async def send_document(
            self,
            chat_id,
            document,
            filename=None,
            caption=None,
            **kwargs,
        ):
            sent_documents.append((chat_id, document, filename, caption, kwargs))
            return SimpleNamespace(id="doc")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.push_file_enabled = True
    worker.push_file_threshold = 20

    ok = await worker._push_to_target("telegram", "101", "A" * 200)
    assert ok is True
    assert sent_messages == []
    assert len(sent_documents) == 1
    chat_id, document, filename, caption, _kwargs = sent_documents[0]
    assert chat_id == "101"
    assert isinstance(document, (bytes, bytearray))
    assert b"AAAA" in bytes(document)
    assert str(filename).endswith((".md", ".html"))
    assert "完整结果见附件" in str(caption)


@pytest.mark.asyncio
async def test_heartbeat_push_fallbacks_to_chunked_text_when_attachment_fails(
    monkeypatch,
):
    sent_messages = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent_messages.append((chat_id, text, kwargs))
            return SimpleNamespace(id="sent")

        async def send_document(
            self,
            chat_id,
            document,
            filename=None,
            caption=None,
            **kwargs,
        ):
            raise RuntimeError("document channel unavailable")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.push_file_enabled = True
    worker.push_file_threshold = 20

    text = "B" * 120
    ok = await worker._push_to_target("telegram", "202", text)
    assert ok is True
    assert sent_messages
    assert sent_messages[0][0] == "202"
    assert sent_messages[0][1] == text


@pytest.mark.asyncio
async def test_heartbeat_push_prefers_chunked_text_for_two_chunks(monkeypatch):
    sent_messages = []
    sent_documents = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            sent_messages.append((chat_id, text, kwargs))
            return SimpleNamespace(id="sent")

        async def send_document(
            self,
            chat_id,
            document,
            filename=None,
            caption=None,
            **kwargs,
        ):
            sent_documents.append((chat_id, document, filename, caption, kwargs))
            return SimpleNamespace(id="doc")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.push_file_enabled = True
    worker.push_file_threshold = 20000
    worker.push_max_text_chunks = 3

    text = "C" * 4200
    ok = await worker._push_to_target("telegram", "303", text)
    assert ok is True
    assert sent_documents == []
    assert len(sent_messages) == 2
    assert sent_messages[0][0] == "303"
    assert sent_messages[0][1].startswith("[1/2]\n")
    assert sent_messages[1][1].startswith("[2/2]\n")


@pytest.mark.asyncio
async def test_heartbeat_specs_skip_auto_rss_when_checklist_already_rss(monkeypatch):
    async def _fake_get_user_subscriptions(_user_id: int):
        return [{"id": 1, "feed_url": "https://example.com/rss.xml", "title": "AI"}]

    monkeypatch.setattr(
        "core.state_store.get_user_subscriptions",
        _fake_get_user_subscriptions,
    )

    worker = HeartbeatWorker()
    worker.enable_rss_signal = True

    specs = await worker._build_heartbeat_task_specs(
        user_id="257675041",
        checklist=["推送最新rss给我"],
    )
    rss_specs = [item for item in specs if str(item.get("type") or "") == "rss_signal"]
    assert len(rss_specs) == 0


@pytest.mark.asyncio
async def test_heartbeat_specs_skip_auto_stock_when_checklist_is_rss(monkeypatch):
    async def _fake_get_user_watchlist(_user_id: int):
        return [{"id": 1, "stock_code": "AAPL", "stock_name": "Apple"}]

    monkeypatch.setattr(
        "core.state_store.get_user_watchlist",
        _fake_get_user_watchlist,
    )
    monkeypatch.setattr("core.scheduler.is_trading_time", lambda: True)

    worker = HeartbeatWorker()
    worker.enable_stock_signal = True

    specs = await worker._build_heartbeat_task_specs(
        user_id="257675041",
        checklist=["检查我的 RSS 订阅更新"],
    )
    stock_specs = [
        item for item in specs if str(item.get("type") or "") == "stock_signal"
    ]
    assert len(stock_specs) == 0


@pytest.mark.asyncio
async def test_heartbeat_rss_goal_prefers_direct_scheduler_refresh(
    monkeypatch, tmp_path
):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    user_id = "worker_rss_1"
    await heartbeat_store.set_heartbeat_spec(
        user_id,
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )
    await heartbeat_store.remove_checklist_item(user_id, 1)
    await heartbeat_store.add_checklist_item(user_id, "检查我的 RSS 订阅更新")
    await heartbeat_store.set_delivery_target(user_id, "telegram", "501")

    refresh_calls = {"value": 0}

    async def _fake_trigger_manual_rss_check(_uid: int):
        refresh_calls["value"] += 1
        return "📢 RSS 订阅日报 (1 条更新)\n\n- AI 新闻更新"

    monkeypatch.setattr(
        "core.scheduler.trigger_manual_rss_check",
        _fake_trigger_manual_rss_check,
    )

    async def _fake_orchestrator(_ctx, _message_history):
        yield "HEARTBEAT_OK"

    monkeypatch.setattr(
        heartbeat_worker_module,
        "agent_orchestrator",
        type(
            "FakeOrchestrator",
            (),
            {"handle_message": _fake_orchestrator},
        )(),
    )

    pushed = []

    class _FakeAdapter:
        async def send_message(self, chat_id, text, **kwargs):
            pushed.append((chat_id, text, kwargs))
            return SimpleNamespace(id="sent")

    monkeypatch.setattr(
        heartbeat_worker_module.adapter_manager,
        "get_adapter",
        lambda _platform: _FakeAdapter(),
    )

    worker = HeartbeatWorker()
    worker.enabled = True
    worker.suppress_ok = True

    result = await worker.run_user_now(user_id)
    assert refresh_calls["value"] == 1
    assert "RSS 订阅日报" in result
    assert pushed
    assert pushed[0][0] == "501"
    assert "RSS 订阅日报" in pushed[0][1]


@pytest.mark.asyncio
async def test_heartbeat_multiple_rss_items_only_append_once(monkeypatch, tmp_path):
    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(heartbeat_store, "root", runtime_root)
    heartbeat_store._locks.clear()

    user_id = "worker_rss_2"
    await heartbeat_store.set_heartbeat_spec(
        user_id,
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )

    call_count = {"value": 0}

    async def _fake_trigger_manual_rss_check(_uid: int):
        call_count["value"] += 1
        return "📢 RSS 订阅日报 (1 条更新)\n\n- AI 新闻更新"

    monkeypatch.setattr(
        "core.scheduler.trigger_manual_rss_check",
        _fake_trigger_manual_rss_check,
    )

    worker = HeartbeatWorker()
    result = await worker._run_heartbeat_task_batch(
        user_id=user_id,
        checklist=["检查我的 RSS 订阅更新", "再检查一次 RSS 订阅更新"],
        owner="test-owner",
    )
    assert call_count["value"] == 1
    assert result.count("RSS 订阅日报") == 1
