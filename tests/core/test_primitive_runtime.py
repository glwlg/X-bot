import pytest

from core.primitive_runtime import PrimitiveRuntime


@pytest.mark.asyncio
async def test_primitive_runtime_read_edit_write_loop(tmp_path):
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))
    test_file = tmp_path / "notes.txt"
    test_file.write_text("line1\nline2\n", encoding="utf-8")

    read_before = await runtime.read("notes.txt")
    assert read_before["ok"] is True
    assert "line2" in read_before["data"]["content"]

    edit_result = await runtime.edit(
        "notes.txt",
        edits=[{"old_text": "line2", "new_text": "line2-updated"}],
    )
    assert edit_result["ok"] is True
    assert edit_result["data"]["changed"] is True

    append_result = await runtime.write(
        "notes.txt",
        content="line3\n",
        mode="append",
    )
    assert append_result["ok"] is True

    read_after = await runtime.read("notes.txt")
    assert read_after["ok"] is True
    assert "line2-updated" in read_after["data"]["content"]
    assert "line3" in read_after["data"]["content"]


@pytest.mark.asyncio
async def test_primitive_runtime_bash_timeout(tmp_path):
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))

    result = await runtime.bash("sleep 2", timeout_sec=1)

    assert result["ok"] is False
    assert result["error_code"] == "timeout"


@pytest.mark.asyncio
async def test_primitive_runtime_bash_success(tmp_path):
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))

    result = await runtime.bash("echo hello")

    assert result["ok"] is True
    assert result["data"]["exit_code"] == 0
    assert "hello" in result["data"]["output"]


@pytest.mark.asyncio
async def test_primitive_runtime_bash_nonzero_exit_is_failure(tmp_path):
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))

    result = await runtime.bash("sh -c 'exit 7'")

    assert result["ok"] is False
    assert result["error_code"] == "command_failed"
    assert result["data"]["exit_code"] == 7


@pytest.mark.asyncio
async def test_primitive_runtime_blocks_kernel_protected_write(monkeypatch, tmp_path):
    monkeypatch.setenv("KERNEL_PROTECTED_PATHS", "kernel")
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))

    result = await runtime.write("kernel/a.txt", content="x")

    assert result["ok"] is False
    assert result["error_code"] == "policy_blocked"


@pytest.mark.asyncio
async def test_primitive_runtime_blocks_kernel_protected_edit(monkeypatch, tmp_path):
    monkeypatch.setenv("KERNEL_PROTECTED_PATHS", "kernel")
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))
    target = tmp_path / "kernel" / "a.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello", encoding="utf-8")

    result = await runtime.edit(
        "kernel/a.txt",
        edits=[{"old_text": "hello", "new_text": "world"}],
    )

    assert result["ok"] is False
    assert result["error_code"] == "policy_blocked"


@pytest.mark.asyncio
async def test_primitive_runtime_blocks_kernel_protected_bash_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("KERNEL_PROTECTED_PATHS", "kernel")
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))
    (tmp_path / "kernel").mkdir(parents=True, exist_ok=True)

    result = await runtime.bash("pwd", cwd="kernel")

    assert result["ok"] is False
    assert result["error_code"] == "policy_blocked"
