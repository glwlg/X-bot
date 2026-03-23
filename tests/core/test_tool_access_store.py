from core.tool_access_store import ToolAccessStore


def test_tool_access_groups_and_defaults(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    groups = store.groups_for_tool("read", kind="tool")
    assert "group:fs" in groups
    assert "group:primitives" in groups

    delivery_groups = store.groups_for_tool("send_local_file", kind="tool")
    assert "group:delivery" in delivery_groups
    assert "group:fs" in delivery_groups

    bash_groups = store.groups_for_tool("bash", kind="tool")
    assert "group:execution" in bash_groups

    coding_tool_groups = store.groups_for_tool("coding_backend", kind="tool")
    assert "group:coding" in coding_tool_groups

    generic_skill_groups = store.groups_for_tool("ext_internal_dev_tool", kind="tool")
    assert "group:coding" not in generic_skill_groups

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="subagent::subagent-main::u1",
        platform="subagent_kernel",
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
        lambda name: (
            {
                "name": "queue_status",
                "skill_name": "skill_manager",
            }
            if name == "queue_status"
            else None
        ),
    )

    groups = store.groups_for_tool("queue_status", kind="tool")

    assert "group:skills" in groups
    assert "group:skill-admin" in groups


def test_tool_access_ext_skill_prefers_frontmatter_policy_groups(tmp_path, monkeypatch):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    monkeypatch.setattr(
        "core.skill_loader.skill_loader.get_skill",
        lambda name: (
            {"policy_groups": ["group:media"]} if name == "download_video" else {}
        ),
    )

    groups = store.groups_for_tool("ext_download_video", kind="tool")

    assert "group:media" in groups


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
    assert allowed is True
    assert detail["reason"] == "allowed"

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
        tool_name="ext_skill_manager",
        kind="tool",
    )
    assert allowed_manager_skill is True
    assert "group:skill-admin" in manager_skill_detail["groups"]

    denied_finance_skill, finance_skill_detail = store.is_tool_allowed(
        runtime_user_id="u-plain-user",
        platform="telegram",
        tool_name="ext_stock_watch",
        kind="tool",
    )
    assert denied_finance_skill is True
    assert finance_skill_detail["reason"] == "allowed"


def test_subagent_runtime_uses_manager_policy_without_management_loops(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    resolved = store.resolve_runtime_policy(
        runtime_user_id="subagent::subagent-main::u-1",
        platform="subagent_kernel",
    )
    assert resolved["agent_kind"] == "subagent"

    allowed_dev, _ = store.is_tool_allowed(
        runtime_user_id="subagent::subagent-main::u-1",
        platform="subagent_kernel",
        tool_name="git_ops",
        kind="tool",
    )
    assert allowed_dev is True

    denied_spawn, detail = store.is_tool_allowed(
        runtime_user_id="subagent::subagent-main::u-1",
        platform="subagent_kernel",
        tool_name="spawn_subagent",
        kind="tool",
    )
    assert denied_spawn is True
    assert "group:management" in detail["groups"]
