import time
from types import SimpleNamespace

import pytest

from core.extension_router import ExtensionCandidate
from core.orchestrator_runtime_tools import ToolCallDispatcher
import core.orchestrator_runtime_tools as runtime_tools_module


@pytest.mark.asyncio
async def test_tool_dispatcher_replans_extension_args_after_invalid_args(monkeypatch):
    calls = []

    async def fake_run_extension(*, skill_name, args, ctx, runtime):
        _ = (ctx, runtime)
        calls.append((skill_name, dict(args or {})))
        if not args.get("url"):
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "missing required field(s): url",
                "failure_mode": "recoverable",
            }
        return {"ok": True, "text": "done"}

    plans = [
        {
            "args": {},
            "missing_fields": ["url"],
            "planned": True,
            "source": "llm",
            "reason": "first pass",
        },
        {
            "args": {"url": "https://example.com", "action": "summarize"},
            "missing_fields": [],
            "planned": True,
            "source": "llm",
            "reason": "retry pass",
        },
    ]

    async def fake_plan(**_kwargs):
        if plans:
            return plans.pop(0)
        return {
            "args": {},
            "missing_fields": [],
            "planned": False,
            "source": "heuristic",
            "reason": "",
        }

    monkeypatch.setattr(
        runtime_tools_module.extension_tools,
        "run_extension",
        fake_run_extension,
    )
    monkeypatch.setattr(
        runtime_tools_module.skill_arg_planner,
        "plan",
        fake_plan,
    )

    async def append_event(_event: str):
        return None

    dispatcher = ToolCallDispatcher(
        runtime_user_id="worker::worker-main::u1",
        platform_name="worker_runtime",
        task_id="task-1",
        task_inbox_id="",
        task_workspace_root="/tmp",
        ctx=SimpleNamespace(
            message=SimpleNamespace(text="请总结 https://example.com"),
            user_data={},
        ),
        runtime=object(),
        tool_broker=object(),
        runtime_tool_allowed=lambda **_kwargs: True,
        record_tool_profile=lambda *_args, **_kwargs: None,
        todo_mark_step=lambda *_args, **_kwargs: None,
        append_session_event=append_event,
    )
    dispatcher.set_extension_candidates(
        [
            ExtensionCandidate(
                name="web_browser",
                description="",
                tool_name="ext_web_browser",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["url"],
                },
                schema_summary="",
                triggers=[],
            )
        ]
    )
    dispatcher.set_available_tool_names({"ext_web_browser"})

    result = await dispatcher.execute(
        name="ext_web_browser",
        args={},
        execution_policy=None,
        started=time.perf_counter(),
    )

    assert result["ok"] is True
    assert len(calls) == 2
    assert calls[-1][1]["url"] == "https://example.com"
    assert result["arg_planner"]["attempt"] == 2
