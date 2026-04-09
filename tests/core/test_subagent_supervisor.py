from types import SimpleNamespace

import pytest

from core.subagent_supervisor import SubagentSupervisor
from core.subagent_types import SubagentResult


@pytest.mark.asyncio
async def test_await_subagents_returns_collected_status_with_failure_counts():
    supervisor = SubagentSupervisor()
    supervisor._runs["subagent-1"] = SimpleNamespace(
        subagent_id="subagent-1",
        task=None,
        result=SubagentResult(
            subagent_id="subagent-1",
            ok=False,
            summary="未能加载 article_publisher",
            text="未能加载 article_publisher",
            error="未能加载 article_publisher",
            diagnostic_summary="未能加载 article_publisher",
            task_outcome="blocked",
            failure_mode="recoverable",
            ikaros_followup_required=True,
        ),
    )

    result = await supervisor.await_subagents(subagent_ids=["subagent-1"])

    assert result["ok"] is True
    assert result["terminal"] is False
    assert result["task_outcome"] == "collected"
    assert result["payload"]["all_completed"] is True
    assert result["payload"]["success_count"] == 0
    assert result["payload"]["failure_count"] == 1
    assert "0 个成功、1 个失败" in result["summary"]
