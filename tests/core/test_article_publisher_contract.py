from extension.skills.registry import skill_registry
import pytest
from types import SimpleNamespace

from core.orchestrator_runtime_tools import ToolCallDispatcher


def test_article_publisher_contract_targets_subagent_runtime():
    skill_info = skill_registry.get_skill("article_publisher")

    assert skill_info is not None
    assert skill_info["contract"]["runtime_target"] == "subagent"
    assert skill_info["contract"]["rollout_target"] == "subagent"


@pytest.mark.asyncio
async def test_subagent_runtime_can_load_article_publisher_skill():
    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="subagent::subagent-main::u1",
        platform_name="subagent_kernel",
        task_id="task-article-subagent",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(
                text="写公众号文章",
                user=SimpleNamespace(id="source-user-1"),
                chat=SimpleNamespace(id="chat-99"),
            ),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_available_tool_names({"load_skill"})

    result = await dispatcher.execute(
        name="load_skill",
        args={"skill_name": "article_publisher"},
        execution_policy=None,
        started=0.0,
    )

    assert result["ok"] is True
    assert "Article Publisher" in str(result.get("content") or "")
