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


def test_prompt_composer_filters_skill_catalog_by_runtime_policy(monkeypatch):
    monkeypatch.setattr(
        "core.skill_loader.skill_loader.get_skills_summary",
        lambda: [
            {
                "name": "stock_watch",
                "description": "行情查询",
                "allowed_roles": [],
            },
            {
                "name": "worker_management",
                "description": "worker 调度",
                "allowed_roles": ["manager"],
            },
            {
                "name": "skill_manager",
                "description": "技能治理",
                "allowed_roles": [],
            },
        ],
    )

    def fake_allowed(**kwargs):
        return (
            kwargs["tool_name"] in {"ext_worker_management", "ext_skill_manager"},
            {},
        )

    monkeypatch.setattr(
        "core.tool_access_store.tool_access_store.is_tool_allowed",
        fake_allowed,
    )

    text = prompt_composer._build_skill_catalog(
        runtime_user_id="u-1",
        platform="telegram",
    )

    assert "worker_management" in text
    assert "skill_manager" in text
    assert "stock_watch" not in text


def test_prompt_composer_hides_manager_only_roles_from_worker(monkeypatch):
    monkeypatch.setattr(
        "core.skill_loader.skill_loader.get_skills_summary",
        lambda: [
            {
                "name": "stock_watch",
                "description": "行情查询",
                "allowed_roles": [],
            },
            {
                "name": "worker_management",
                "description": "worker 调度",
                "allowed_roles": ["manager"],
            },
        ],
    )
    monkeypatch.setattr(
        "core.tool_access_store.tool_access_store.is_tool_allowed",
        lambda **_kwargs: (True, {}),
    )

    text = prompt_composer._build_skill_catalog(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_kernel",
    )

    assert "stock_watch" in text
    assert "worker_management" not in text
