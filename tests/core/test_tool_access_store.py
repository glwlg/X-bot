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

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u1",
        tool_name="ext_web_browser",
        kind="tool",
    )
    assert allowed is True
    assert "group:research" in detail["groups"]
    assert "group:skills" in detail["groups"]


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
    allowed_after, _ = store.is_backend_allowed(worker_id="worker-main", backend="codex")
    assert allowed_after is True


def test_tool_access_core_policy_is_readonly(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    ok, reason = store.set_worker_policy("core-manager", allow=["group:all"], deny=[])
    assert ok is False
    assert reason == "core_manager_policy_is_readonly"


def test_regular_user_runtime_uses_worker_default_policy(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()

    resolved = store.resolve_runtime_policy(runtime_user_id="u-plain-user", platform="telegram")
    assert resolved["agent_kind"] == "worker-default"

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="u-plain-user",
        platform="telegram",
        tool_name="ext_web_browser",
        kind="tool",
    )
    assert allowed is True
    assert detail["reason"] == "allowed"


def test_worker_runtime_still_uses_worker_policy(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()
    store.ensure_worker_policy("worker-main")

    resolved = store.resolve_runtime_policy(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_runtime",
    )
    assert resolved["agent_kind"] == "worker"

    allowed, _ = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_runtime",
        tool_name="ext_web_browser",
        kind="tool",
    )
    assert allowed is True


def test_worker_runtime_memory_is_hard_disabled(tmp_path):
    store = ToolAccessStore()
    store.path = (tmp_path / "tool_access.json").resolve()
    store._payload = store._default_payload()
    store._write_unlocked()
    store.ensure_worker_policy("worker-main")

    allowed, detail = store.is_tool_allowed(
        runtime_user_id="worker::worker-main::u-1",
        platform="worker_runtime",
        tool_name="open_nodes",
        kind="mcp",
    )
    assert allowed is False
    assert detail["reason"] == "worker_memory_disabled"
