from core.prompt_composer import prompt_composer


def test_prompt_composer_minimal_shape():
    text = prompt_composer.compose_base(
        runtime_user_id="123",
        tools=[
            {"name": "read", "description": "Read file content"},
            {"name": "ext_searxng_search", "description": "Search web results"},
        ],
        runtime_policy_ctx={
            "agent_kind": "core-manager",
            "policy": {"tools": {"allow": ["group:all"], "deny": []}},
        },
        mode="manager",
    )

    assert "【SOUL】" in text
    assert "【可用工具】" in text
    assert "`read`" in text
    assert "`ext_searxng_search`" in text
    assert "list_workers" in text
    assert "dispatch_worker" in text
    assert "【记忆管理指南】" in text
    assert "【工具使用原则】" not in text
    assert "【运行环境事实】" not in text
    assert "【身份与记忆决策】" not in text
    assert "【MODE】manager" in text


def test_prompt_composer_includes_memory_guide_for_core_manager():
    text = prompt_composer.compose_base(
        runtime_user_id="123",
        tools=[
            {"name": "read", "description": "Read file content"},
        ],
        runtime_policy_ctx={
            "agent_kind": "core-manager",
            "policy": {"tools": {"allow": ["group:all"], "deny": []}},
        },
        mode="chat",
    )

    assert "【记忆管理指南】" in text


def test_prompt_composer_worker_runtime_omits_memory_guide():
    text = prompt_composer.compose_base(
        runtime_user_id="worker::worker-main::u1",
        tools=[
            {"name": "read", "description": "Read file content"},
        ],
        runtime_policy_ctx={
            "agent_kind": "worker",
            "policy": {"tools": {"allow": ["group:all"], "deny": []}},
        },
        mode="chat",
    )

    assert "【记忆管理指南】" not in text
    assert "memory.*" not in text
