from core.tool_access_store import ToolAccessStore


def test_tool_access_groups_and_defaults(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    groups = store.groups_for_tool("read", kind="tool")
    assert "group:fs" in groups
    assert "group:primitives" in groups

    bash_groups = store.groups_for_tool("bash", kind="tool")
    assert "group:execution" in bash_groups

    coding_tool_groups = store.groups_for_tool("coding_backend", kind="tool")
    assert "group:coding" in coding_tool_groups

    generic_skill_groups = store.groups_for_tool("ext_internal_dev_tool", kind="tool")
    assert "group:coding" not in generic_skill_groups

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u1",
        tool_name="ext_web_browser",
        kind="tool",
    )
    assert allowed is True
    assert "group:research" in detail["groups"]
    assert "group:skills" in detail["groups"]


def test_tool_access_dynamic_skill_export_inherits_parent_skill_groups(
    tmp_path,
    monkeypatch,
):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    monkeypatch.setattr(
        "core.skill_loader.skill_loader.get_tool_export",
        lambda name: {
            "name": "queue_status",
            "skill_name": "worker_management",
        }
        if name == "queue_status"
        else None,
    )

    groups = store.groups_for_tool("queue_status", kind="tool")

    assert "group:skills" in groups
    assert "group:execution" in groups
    assert "group:management" in groups


def test_tool_access_ext_skill_prefers_frontmatter_policy_groups(tmp_path, monkeypatch):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    monkeypatch.setattr(
        "core.skill_loader.skill_loader.get_skill",
        lambda name: {"policy_groups": ["group:media"]}
        if name == "download_video"
        else {},
    )

    groups = store.groups_for_tool("ext_download_video", kind="tool")

    assert "group:media" in groups


def test_tool_access_worker_policy_denies_coding(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()
    store.ensure_worker_policy("worker-main")

    allowed, detail = store.is_backend_allowed(worker_id="worker-main", backend="codex")
    assert allowed is False
    assert detail["reason"] in {"matched_deny_list", "not_in_allow_list"}

    ok, reason = store.set_worker_policy(
        "worker-main",
        allow=["group:all"],
        deny=[],
    )
    assert ok is True
    assert reason == "updated"
    allowed_after, _ = store.is_backend_allowed(
        worker_id="worker-main", backend="codex"
    )
    assert allowed_after is True


def test_tool_access_core_policy_is_readonly(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    ok, reason = store.set_worker_policy("core-manager", allow=["group:all"], deny=[])
    assert ok is False
    assert reason == "core_manager_policy_is_readonly"


def test_regular_user_runtime_uses_core_manager_policy(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    resolved = store.resolve_runtime_policy(
        runtime_user_id="u-plain-user", platform="telegram"
    )
    assert resolved["agent_kind"] == "core-manager"

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="u-plain-user",
        platform="telegram",
        tool_name="ext_web_browser",
        kind="tool",
    )
    assert allowed is False
    assert detail["reason"] in {"not_in_allow_list", "matched_deny_list"}

    allowed_management, management_detail = store.is_tool_allowed(
        runtime_user_id="u-plain-user",
        platform="telegram",
        tool_name="git_ops",
        kind="tool",
    )
    assert allowed_management is True
    assert "group:management" in management_detail["groups"]

    allowed_primitive, primitive_detail = store.is_tool_allowed(
        runtime_user_id="u-plain-user",
        platform="telegram",
        tool_name="read",
        kind="tool",
    )
    assert allowed_primitive is True
    assert "group:primitives" in primitive_detail["groups"]

    allowed_manager_skill, manager_skill_detail = store.is_tool_allowed(
        runtime_user_id="u-plain-user",
        platform="telegram",
        tool_name="ext_worker_management",
        kind="tool",
    )
    assert allowed_manager_skill is True
    assert "group:management" in manager_skill_detail["groups"]

    denied_finance_skill, finance_skill_detail = store.is_tool_allowed(
        runtime_user_id="u-plain-user",
        platform="telegram",
        tool_name="ext_stock_watch",
        kind="tool",
    )
    assert denied_finance_skill is False
    assert finance_skill_detail["reason"] in {"not_in_allow_list", "matched_deny_list"}


def test_worker_kernel_still_uses_worker_policy(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()
    store.ensure_worker_policy("worker-main")

    resolved = store.resolve_runtime_policy(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_kernel",
    )
    assert resolved["agent_kind"] == "worker"

    allowed, _ = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_kernel",
        tool_name="ext_web_browser",
        kind="tool",
    )
    assert allowed is True


def test_worker_default_policy_denies_management_tools(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()
    store.ensure_worker_policy("worker-main")

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_kernel",
        tool_name="dispatch_worker",
        kind="tool",
    )
    assert allowed is False
    assert detail["reason"] in {"matched_deny_list", "not_in_allow_list"}

    denied_dev, denied_dev_detail = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_kernel",
        tool_name="git_ops",
        kind="tool",
    )
    assert denied_dev is False
    assert denied_dev_detail["reason"] in {"matched_deny_list", "not_in_allow_list"}

    denied_skill_admin, denied_skill_admin_detail = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_kernel",
        tool_name="ext_skill_manager",
        kind="tool",
    )
    assert denied_skill_admin is False
    assert denied_skill_admin_detail["reason"] in {
        "matched_deny_list",
        "not_in_allow_list",
    }


def test_worker_kernel_memory_is_hard_disabled(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()
    store.ensure_worker_policy("worker-main")

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_kernel",
        tool_name="open_nodes",
        kind="mcp",
    )
    assert allowed is False
    assert detail["reason"] == "worker_memory_disabled"
