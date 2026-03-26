from __future__ import annotations

from pathlib import Path

import pytest

from core.heartbeat_store import heartbeat_store
from core.task_inbox import task_inbox
from ikaros.planning.stage_planner import normalize_stage_plan
import ikaros.relay.closure_service as closure_module
from shared.contracts.dispatch import TaskEnvelope


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


@pytest.fixture
def _isolated_state(tmp_path):
    _reset_task_inbox(tmp_path)
    _reset_heartbeat_store(tmp_path)
    return tmp_path


def _two_stage_plan(original_request: str) -> dict:
    plan = normalize_stage_plan(None, original_request=original_request)
    stages = [
        {
            "id": "stage-1",
            "title": "收集信息",
            "goal": "先明确约束并收集执行所需信息。",
            "success_signal": "进入执行阶段所需信息已具备。",
            "executor": "subagent",
            "status": "running",
            "attempt_count": 1,
            "last_summary": "",
            "last_output": "",
            "last_error": "",
        },
        {
            "id": "stage-2",
            "title": "执行主要任务",
            "goal": "完成主体执行并整理输出。",
            "success_signal": "主体执行已经完成。",
            "executor": "subagent",
            "status": "pending",
            "attempt_count": 0,
            "last_summary": "",
            "last_output": "",
            "last_error": "",
        },
    ]
    plan["stages"] = stages
    plan["current_stage_id"] = "stage-1"
    plan["completed_stage_ids"] = []
    plan["blocked_stage_id"] = ""
    plan["attempt_count"] = 1
    return plan


@pytest.mark.asyncio
async def test_resolve_attempt_blocked_moves_session_to_waiting_user(
    monkeypatch, _isolated_state
):
    session = await task_inbox.submit(
        source="user_chat",
        goal="帮我修复并验证部署流程",
        user_id="u-1",
        metadata={
            "original_user_request": "帮我修复并验证部署流程",
            "stage_plan": _two_stage_plan("帮我修复并验证部署流程"),
        },
    )
    await heartbeat_store.set_session_active_task(
        "u-1",
        {
            "id": "mgr-1",
            "session_task_id": session.task_id,
            "task_inbox_id": session.task_id,
            "goal": session.goal,
            "status": "running",
            "source": "user_chat",
            "stage_index": 1,
            "stage_total": 2,
            "stage_id": "stage-1",
            "stage_title": "收集信息",
            "attempt_index": 1,
            "needs_confirmation": False,
            "confirmation_deadline": "",
        },
    )

    task = TaskEnvelope(
        task_id="attempt-1",
        executor_id="subagent-main",
        instruction=session.goal,
        source="subagent",
        metadata={
            "user_id": "u-1",
            "platform": "telegram",
            "chat_id": "chat-1",
            "task_inbox_id": session.task_id,
            "session_task_id": session.task_id,
            "staged_session": True,
            "stage_id": "stage-1",
            "stage_title": "收集信息",
            "stage_index": 1,
            "stage_total": 2,
            "attempt_index": 1,
            "original_user_request": session.goal,
        },
    )
    result = {
        "ok": False,
        "summary": "工具调用预算耗尽",
        "error": "工具调用预算耗尽",
        "payload": {
            "text": "工具调用预算耗尽",
            "attempt_outcome": "blocked",
            "closure_reason": "max_turn_limit",
            "failure_mode": "recoverable",
            "diagnostic_summary": "工具调用预算耗尽",
            "progress_snapshot": {"turn": 20, "failed_tools": ["bash"]},
        },
    }

    decision = await closure_module.ikaros_closure_service.resolve_attempt(
        task=task,
        result=result,
        platform="telegram",
        chat_id="chat-1",
    )

    assert decision["kind"] == "waiting_user"
    assert "回复“继续”" in decision["text"]
    assert decision["ui"]["actions"][0][0]["callback_data"] == "task_continue"

    stored = await task_inbox.get(session.task_id)
    assert stored is not None
    assert stored.status == "waiting_user"
    assert stored.metadata["delivery_state"] == "pending"
    assert "任务暂时卡住了" in str(stored.metadata["last_user_visible_summary"])
    active = await heartbeat_store.get_session_active_task("u-1")
    assert active is not None
    assert active["status"] == "waiting_user"
    assert active["session_task_id"] == session.task_id
    assert active["delivery_state"] == "pending"


@pytest.mark.asyncio
async def test_resolve_attempt_success_dispatches_next_stage(monkeypatch, _isolated_state):
    session = await task_inbox.submit(
        source="user_chat",
        goal="帮我修复并验证部署流程",
        user_id="u-2",
        metadata={
            "original_user_request": "帮我修复并验证部署流程",
            "stage_plan": _two_stage_plan("帮我修复并验证部署流程"),
        },
    )
    await heartbeat_store.set_session_active_task(
        "u-2",
        {
            "id": "mgr-2",
            "session_task_id": session.task_id,
            "task_inbox_id": session.task_id,
            "goal": session.goal,
            "status": "running",
            "source": "user_chat",
            "stage_index": 1,
            "stage_total": 2,
            "stage_id": "stage-1",
            "stage_title": "收集信息",
            "attempt_index": 1,
            "needs_confirmation": False,
            "confirmation_deadline": "",
        },
    )

    calls: list[dict] = []

    async def fake_spawn(**kwargs):
        calls.append(dict(kwargs))
        return {
            "ok": True,
            "subagent_id": "subagent-stage-2",
            "task_id": "tsk-stage-2",
        }

    monkeypatch.setattr(
        closure_module.subagent_supervisor,
        "spawn",
        fake_spawn,
    )

    task = TaskEnvelope(
        task_id="attempt-2",
        executor_id="subagent-main",
        instruction=session.goal,
        source="subagent",
        metadata={
            "user_id": "u-2",
            "platform": "telegram",
            "chat_id": "chat-2",
            "task_inbox_id": session.task_id,
            "session_task_id": session.task_id,
            "staged_session": True,
            "stage_id": "stage-1",
            "stage_title": "收集信息",
            "stage_index": 1,
            "stage_total": 2,
            "attempt_index": 1,
            "original_user_request": session.goal,
        },
    )
    result = {
        "ok": True,
        "summary": "信息已整理完毕",
        "payload": {
            "text": "信息已整理完毕",
            "attempt_outcome": "done",
            "diagnostic_summary": "信息已整理完毕",
        },
    }

    decision = await closure_module.ikaros_closure_service.resolve_attempt(
        task=task,
        result=result,
        platform="telegram",
        chat_id="chat-2",
    )

    assert decision["kind"] == "next_stage"
    assert "正在继续" in decision["text"]
    assert len(calls) == 1
    assert calls[0]["mode"] == "detached"
    assert calls[0]["task_metadata"]["stage_id"] == "stage-2"

    stored = await task_inbox.get(session.task_id)
    assert stored is not None
    assert stored.status == "running"
    stage_plan = dict((stored.metadata or {}).get("stage_plan") or {})
    assert stage_plan["current_stage_id"] == "stage-2"
    assert stored.metadata["subagent_ids"] == ["subagent-stage-2"]


@pytest.mark.asyncio
async def test_resolve_attempt_final_stage_non_deliverable_text_becomes_waiting_user(
    _isolated_state,
):
    plan = normalize_stage_plan(None, original_request="帮我整理最新 AI 简报")
    plan["stages"] = [
        {
            "id": "stage-1",
            "title": "验证结果并整理交付",
            "goal": "整理最终简报",
            "success_signal": "结果已验证，并具备最终交付所需的摘要或附件。",
            "executor": "subagent",
            "status": "running",
            "attempt_count": 1,
            "last_summary": "",
            "last_output": "",
            "last_error": "",
        }
    ]
    plan["current_stage_id"] = "stage-1"

    session = await task_inbox.submit(
        source="user_chat",
        goal="帮我整理最新 AI 简报",
        user_id="u-final",
        metadata={
            "original_user_request": "帮我整理最新 AI 简报",
            "stage_plan": plan,
        },
    )
    await heartbeat_store.set_session_active_task(
        "u-final",
        {
            "id": "mgr-final",
            "session_task_id": session.task_id,
            "task_inbox_id": session.task_id,
            "goal": session.goal,
            "status": "running",
            "source": "user_chat",
            "stage_index": 1,
            "stage_total": 1,
            "stage_id": "stage-1",
            "stage_title": "验证结果并整理交付",
            "attempt_index": 1,
            "needs_confirmation": False,
            "confirmation_deadline": "",
        },
    )

    task = TaskEnvelope(
        task_id="attempt-final-1",
        executor_id="subagent-main",
        instruction=session.goal,
        source="subagent",
        metadata={
            "user_id": "u-final",
            "platform": "telegram",
            "chat_id": "chat-final",
            "task_inbox_id": session.task_id,
            "session_task_id": session.task_id,
            "staged_session": True,
            "stage_id": "stage-1",
            "stage_title": "验证结果并整理交付",
            "stage_index": 1,
            "stage_total": 1,
            "attempt_index": 1,
            "original_user_request": "帮我整理最新 AI 简报",
        },
    )
    result = {
        "ok": True,
        "summary": "当前不具备直接向用户交付“最新 AI 简报”正文的条件。",
        "payload": {
            "text": (
                "## 最终结果\n"
                "当前不具备直接向用户交付“最新 AI 简报”正文的条件。\n\n"
                "如果需要继续，我下一步应先重新检索并校验，再产出最终可发给用户的中文简报。"
            ),
            "attempt_outcome": "done",
            "diagnostic_summary": "最后阶段没有形成最终正文。",
        },
    }

    decision = await closure_module.ikaros_closure_service.resolve_attempt(
        task=task,
        result=result,
        platform="telegram",
        chat_id="chat-final",
    )

    assert decision["kind"] == "waiting_user"
    assert "回复“继续”" in decision["text"]

    stored = await task_inbox.get(session.task_id)
    assert stored is not None
    assert stored.status == "waiting_user"


@pytest.mark.asyncio
async def test_resolve_attempt_final_completes_session(monkeypatch, _isolated_state, tmp_path):
    report = (tmp_path / "report.md").resolve()
    report.write_text("done", encoding="utf-8")

    session = await task_inbox.submit(
        source="user_chat",
        goal="整理部署结果",
        user_id="u-3",
        metadata={
            "original_user_request": "整理部署结果",
            "stage_plan": normalize_stage_plan(None, original_request="整理部署结果"),
        },
    )
    await heartbeat_store.set_session_active_task(
        "u-3",
        {
            "id": "mgr-3",
            "session_task_id": session.task_id,
            "task_inbox_id": session.task_id,
            "goal": session.goal,
            "status": "running",
            "source": "user_chat",
            "stage_index": 1,
            "stage_total": 1,
            "stage_id": "stage-1",
            "stage_title": "执行任务",
            "attempt_index": 1,
            "needs_confirmation": False,
            "confirmation_deadline": "",
        },
    )

    task = TaskEnvelope(
        task_id="attempt-3",
        executor_id="subagent-main",
        instruction=session.goal,
        source="subagent",
        metadata={
            "user_id": "u-3",
            "platform": "telegram",
            "chat_id": "chat-3",
            "task_inbox_id": session.task_id,
            "session_task_id": session.task_id,
            "staged_session": True,
            "stage_id": "stage-1",
            "stage_title": "执行任务",
            "stage_index": 1,
            "stage_total": 1,
            "attempt_index": 1,
            "original_user_request": session.goal,
        },
    )
    result = {
        "ok": True,
        "summary": "整理完成",
        "payload": {
            "text": "整理完成",
            "attempt_outcome": "done",
            "files": [
                {
                    "kind": "document",
                    "path": str(report),
                    "filename": "report.md",
                }
            ],
        },
    }

    decision = await closure_module.ikaros_closure_service.resolve_attempt(
        task=task,
        result=result,
        platform="telegram",
        chat_id="chat-3",
    )

    assert decision["kind"] == "final"
    assert decision["result"]["payload"]["files"][0]["filename"] == "report.md"
    assert decision["result"]["payload"]["delivery_mode"] == "full_text"
    assert decision["result"]["payload"]["user_facing_output"] is True
    stored = await task_inbox.get(session.task_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.metadata["delivery_state"] == "pending"
    assert str(stored.metadata.get("resume_window_until") or "").strip()
    active = await heartbeat_store.get_session_active_task("u-3")
    assert active is None


@pytest.mark.asyncio
async def test_resume_waiting_task_treats_text_as_adjustment(monkeypatch, _isolated_state):
    session = await task_inbox.submit(
        source="user_chat",
        goal="帮我修复并验证部署流程",
        user_id="u-4",
        metadata={
            "original_user_request": "帮我修复并验证部署流程",
            "stage_plan": _two_stage_plan("帮我修复并验证部署流程"),
        },
    )
    await task_inbox.update_status(
        session.task_id,
        "waiting_user",
        event="stage_blocked",
        detail="blocked",
    )
    await heartbeat_store.set_delivery_target("u-4", "telegram", "chat-4")
    await heartbeat_store.set_session_active_task(
        "u-4",
        {
            "id": "mgr-4",
            "session_task_id": session.task_id,
            "task_inbox_id": session.task_id,
            "goal": session.goal,
            "status": "waiting_user",
            "source": "user_chat",
            "stage_index": 1,
            "stage_total": 2,
            "stage_id": "stage-1",
            "stage_title": "收集信息",
            "attempt_index": 1,
            "needs_confirmation": True,
            "confirmation_deadline": "",
            "last_blocking_reason": "工具调用预算耗尽",
        },
    )

    calls: list[dict] = []

    async def fake_spawn(**kwargs):
        calls.append(dict(kwargs))
        return {
            "ok": True,
            "subagent_id": "subagent-resume-1",
            "task_id": "tsk-resume-1",
        }

    monkeypatch.setattr(
        closure_module.subagent_supervisor,
        "spawn",
        fake_spawn,
    )

    resume = await closure_module.ikaros_closure_service.resume_waiting_task(
        user_id="u-4",
        user_message="把范围限制在最近 7 天，并先检查现有容器状态",
        source="text",
    )

    assert resume["ok"] is True
    assert "已收到你的回复" in resume["message"]
    assert len(calls) == 1
    metadata = dict(calls[0].get("task_metadata") or {})
    stage_plan = dict(metadata.get("stage_plan") or {})
    adjustments = list(stage_plan.get("adjustments") or [])
    assert adjustments[-1]["message"] == "把范围限制在最近 7 天，并先检查现有容器状态"
    assert metadata["task_inbox_id"] == session.task_id
    assert metadata["session_task_id"] == session.task_id
