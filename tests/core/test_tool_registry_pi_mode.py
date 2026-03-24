from core.tool_registry import ToolRegistry


def test_tool_registry_exposes_only_primitives_by_default():
    registry = ToolRegistry()
    tools = registry.get_core_tools()

    names = [tool["name"] for tool in tools]
    assert names == ["read", "write", "edit", "bash"]
    assert "call_skill" not in names


def test_tool_registry_exposes_task_tracker_for_manager_runtime():
    registry = ToolRegistry()

    names = [tool["name"] for tool in registry.get_manager_tools()]

    assert "task_tracker" in names
