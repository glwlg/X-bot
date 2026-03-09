import pytest
from types import SimpleNamespace

import api.api.accounting_router as accounting_router_module
import manager.dispatch.web_accounting_auto_image as web_accounting_module
from shared.contracts.dispatch import TaskEnvelope, TaskResult
from shared.queue.dispatch_queue import DispatchQueue


@pytest.mark.asyncio
async def test_run_web_accounting_auto_image_task_returns_accounting_draft(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    upload_root = tmp_path / "system" / "web_accounting_uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    image_path = upload_root / "receipt.png"
    image_path.write_bytes(b"fake-image")

    async def fake_stream(
        message_history,
        tools=None,
        tool_executor=None,
        system_instruction=None,
        event_callback=None,
    ):
        _ = (message_history, event_callback)
        assert system_instruction is not None
        assert "唯一可用的工具 `submit_accounting_draft`" in system_instruction
        assert [tool["name"] for tool in tools or []] == ["submit_accounting_draft"]
        await tool_executor(
            "submit_accounting_draft",
            {
                "type": "支出",
                "amount": 4.8,
                "category": "日用百货",
                "account": "招商银行信用卡",
                "payee": "拼多多平台商户",
            },
        )
        if False:
            yield ""

    monkeypatch.setattr(
        web_accounting_module._ai_service,
        "generate_response_stream",
        fake_stream,
    )

    task = TaskEnvelope(
        task_id="tsk-web-1",
        worker_id="manager-main",
        instruction="请先识别这张交易图片，再调用 submit_accounting_draft 提交结构化记账草稿。",
        source="web_accounting_auto_image",
        metadata={
            "accounting_user_id": 123,
            "accounting_book_id": 45,
            "accounting_source": "web_clipboard",
            "web_accounting_image_path": str(image_path),
            "web_accounting_image_mime": "image/png",
        },
    )

    result = await web_accounting_module.run_web_accounting_auto_image_task(task)

    assert result.ok is True
    assert result.payload["draft"]["type"] == "支出"
    assert result.payload["draft"]["amount"] == 4.8
    assert result.payload["draft"]["category_name"] == "日用百货"
    assert result.payload["draft"]["account_name"] == "招商银行信用卡"
    assert result.payload["book_id"] == 45
    assert result.payload["tool_called"] == 1


@pytest.mark.asyncio
async def test_run_web_image_quick_accounting_marks_task_delivered_for_api_poll(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MANAGER_DISPATCH_ROOT", raising=False)

    queue = DispatchQueue()
    monkeypatch.setattr(accounting_router_module, "dispatch_queue", queue)

    captured: dict[str, str] = {}

    async def fake_wait(task_id: str, timeout_sec: float) -> dict:
        _ = timeout_sec
        task = await queue.get_task(task_id)
        assert task is not None
        captured["task_id"] = task_id
        captured["delivered_at_before_finish"] = str(task.delivered_at or "")

        claimed = await queue.claim_next(
            worker_id="manager-main",
            claimer="manager-daemon",
        )
        assert claimed is not None
        finished = await queue.finish_task(
            task_id=task_id,
            result=TaskResult(
                task_id=task_id,
                worker_id="manager-main",
                ok=True,
                summary="草稿解析成功",
                payload={
                    "text": "草稿解析成功",
                    "draft": {
                        "type": "支出",
                        "amount": 12.5,
                        "category_name": "餐饮",
                        "account_name": "支付宝",
                    },
                    "book_id": 88,
                },
            ),
        )
        assert finished is not None
        return {
            "ok": True,
            "message": "草稿解析成功",
            "draft": {
                "type": "支出",
                "amount": 12.5,
                "category_name": "餐饮",
                "account_name": "支付宝",
            },
            "book_id": 88,
        }

    monkeypatch.setattr(
        accounting_router_module,
        "_wait_for_dispatch_result",
        fake_wait,
    )

    result = await accounting_router_module._run_web_image_quick_accounting(
        user=SimpleNamespace(id=123),
        book_id=88,
        image_bytes=b"fake-image",
        mime_type="image/png",
        note="午餐",
    )

    assert result["ok"] is True
    assert result["draft"]["account_name"] == "支付宝"
    task = await queue.get_task(captured["task_id"])
    assert task is not None
    assert captured["delivered_at_before_finish"]
    assert task.delivered_at
    undelivered = await queue.list_undelivered(limit=10)
    assert all(item.task_id != captured["task_id"] for item in undelivered)


def test_record_create_from_draft_maps_manager_payload():
    record = accounting_router_module._record_create_from_draft(
        {
            "type": "支出",
            "amount": 18.6,
            "category_name": "餐饮",
            "account_name": "微信",
            "target_account_name": "",
            "payee": "店铺",
            "remark": "午餐",
            "record_time": "2026-03-08 12:30:00",
        }
    )

    assert record.type == "支出"
    assert record.amount == 18.6
    assert record.category_name == "餐饮"
    assert record.account_name == "微信"


@pytest.mark.asyncio
async def test_auto_create_record_from_image_persists_draft(
    monkeypatch,
):
    async def fake_run(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "book_id": 7,
            "draft": {
                "type": "支出",
                "amount": 28.8,
                "category_name": "日用百货",
                "account_name": "招商银行信用卡",
            },
        }

    async def fake_create_record_entity(session, *, book_id, creator_id, data):
        _ = session
        assert book_id == 7
        assert creator_id == 99
        assert data.category_name == "日用百货"
        return SimpleNamespace(id=456)

    async def fake_get_book(*args, **kwargs):
        _ = (args, kwargs)
        return SimpleNamespace(id=7)

    class FakeUpload:
        content_type = "image/png"

        async def read(self):
            return b"fake-image"

    monkeypatch.setattr(
        accounting_router_module,
        "_run_web_image_quick_accounting",
        fake_run,
    )
    monkeypatch.setattr(
        accounting_router_module,
        "_create_record_entity",
        fake_create_record_entity,
    )
    monkeypatch.setattr(
        accounting_router_module,
        "_get_book",
        fake_get_book,
    )

    result = await accounting_router_module.auto_create_record_from_image(
        book_id=7,
        image=FakeUpload(),
        note="",
        user=SimpleNamespace(id=99),
        session=SimpleNamespace(),
    )

    assert result["ok"] is True
    assert result["record_id"] == 456
