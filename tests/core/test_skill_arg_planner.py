from types import SimpleNamespace

import pytest

import core.skill_arg_planner as planner_module


@pytest.mark.asyncio
async def test_skill_arg_planner_uses_heuristic_url_and_action(monkeypatch):
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

    plan = await planner_module.skill_arg_planner.plan(
        skill_name="web_browser",
        current_args={},
        user_request="请总结这个网页 https://example.com/a",
    )

    assert plan["args"]["url"] == "https://example.com/a"
    assert plan["args"]["action"] == "summarize"
    assert plan["missing_fields"] == []
    assert plan["planned"] is False


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

    class _FakeCompletions:
        async def create(self, **_kwargs):
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

    plan = await planner_module.skill_arg_planner.plan(
        skill_name="demo_skill",
        current_args={},
        user_request="执行任务",
    )

    assert plan["planned"] is True
    assert plan["source"] == "llm"
    assert plan["args"]["target"] == "alice"
