from core.prompt_composer import prompt_composer


def test_prompt_composer_minimal_shape():
    text = prompt_composer.compose_base(
        runtime_user_id="123",
        tools=[
            {"name": "read", "description": "Read file content"},
            {"name": "ext_web_search", "description": "Search web results"},
        ],
        runtime_policy_ctx={
            "agent_kind": "core-manager",
            "policy": {"tools": {"allow": ["group:all"], "deny": []}},
        },
        mode="manager",
    )

    assert "【SOUL】" in text


def test_prompt_composer_worker_pool_info_contains_summary_and_skill_hints(monkeypatch):
    monkeypatch.setattr(
        prompt_composer,
        "_infer_worker_extension_skills",
        lambda worker_id: (
            ["generate_image", "download_video"] if worker_id == "worker-main" else []
        ),
    )

    text = prompt_composer._format_worker_list(
        [
            {
                "id": "worker-main",
                "name": "阿黑",
                "status": "ready",
                "backend": "core-agent",
                "capabilities": ["media", "automation"],
                "summary": "图像与多媒体执行助手",
            }
        ]
    )

    assert "worker-main" in text
    assert "图像与多媒体执行助手" in text
    assert "generate_image" in text
