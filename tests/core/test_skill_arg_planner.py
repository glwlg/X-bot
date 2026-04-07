from types import SimpleNamespace

import pytest

import core.skill_arg_planner as planner_module
from core.llm_usage_store import set_current_llm_usage_session_id


@pytest.mark.asyncio
async def test_skill_arg_planner_does_not_guess_args_without_model(monkeypatch):
    monkeypatch.setattr(
        planner_module.skill_loader,
        "get_skill",
        lambda _name: {
            "name": "web_browser",
            "description": "web browser",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["visit", "summarize"]},
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
            "skill_md_content": "",
        },
    )
    monkeypatch.setattr(planner_module, "openai_async_client", None)
    monkeypatch.setattr(planner_module, "get_client_for_model", lambda *_args, **_kwargs: None)

    plan = await planner_module.skill_arg_planner.plan(
        skill_name="web_browser",
        current_args={},
        user_request="请总结这个网页 https://example.com/a",
    )

    assert plan["args"] == {}
    assert plan["missing_fields"] == ["url"]
    assert plan["planned"] is False
    assert plan["source"] == "direct"


@pytest.mark.asyncio
async def test_skill_arg_planner_falls_back_to_model_when_required_missing(monkeypatch):
    monkeypatch.setattr(
        planner_module.skill_loader,
        "get_skill",
        lambda _name: {
            "name": "demo_skill",
            "description": "demo",
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "mode": {"type": "string"},
                },
                "required": ["target"],
            },
            "skill_md_content": "",
        },
    )

    captured_kwargs = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"args":{"target":"alice","mode":"fast"},"missing_fields":[],"reason":"ok"}'
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(planner_module, "openai_async_client", fake_client)
    set_current_llm_usage_session_id("planner-session")

    plan = await planner_module.skill_arg_planner.plan(
        skill_name="demo_skill",
        current_args={},
        user_request="执行任务",
    )

    assert plan["planned"] is True
    assert plan["source"] == "llm"
    assert plan["args"]["target"] == "alice"
    assert captured_kwargs["stream"] is True
    assert captured_kwargs["user"] == "planner-session"
    assert captured_kwargs["extra_body"]["session_id"] == "planner-session"
