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
async def test_primitive_runtime_bash_extracts_saved_files(tmp_path):
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))
    image_path = (tmp_path / "dog.png").resolve()
    image_path.write_bytes(b"png")

    result = await runtime.bash(f"printf '图片已生成\\nsaved_file={image_path}\\n'")

    assert result["ok"] is True
    assert result["terminal"] is True
    assert result["task_outcome"] == "done"
    assert result["payload"]["text"] == "图片已生成"
    assert result["payload"]["files"][0]["kind"] == "photo"
    assert result["payload"]["files"][0]["path"] == str(image_path)
    assert "saved_file=" not in result["data"]["output"]


@pytest.mark.asyncio
async def test_primitive_runtime_bash_nonzero_exit_is_failure(tmp_path):
    runtime = PrimitiveRuntime(workspace_root=str(tmp_path))

    result = await runtime.bash("sh -c 'exit 7'")

    assert result["ok"] is False
    assert result["error_code"] == "command_failed"
    assert result["data"]["exit_code"] == 7


def test_primitive_runtime_classifies_fatal_config_failure_output():
    output = (
        "[stderr]\n"
        "❌ 未配置生图模型。请在 config/models.json 中设置 model.image_generation\n"
    )

    summary = PrimitiveRuntime._summarize_command_failure_output(output)
    failure_mode = PrimitiveRuntime._classify_command_failure_mode(output)

    assert "model.image_generation" in summary
    assert failure_mode == "fatal"


def test_primitive_runtime_classifies_unsupported_image_endpoint_as_fatal():
    output = (
        "[stderr]\n"
        "❌ 当前生图模型对应的接口不支持生图（404 page not found）。"
        "请把 model.image_generation 切换到支持 images.generate 的模型。\n"
    )

    summary = PrimitiveRuntime._summarize_command_failure_output(output)
    failure_mode = PrimitiveRuntime._classify_command_failure_mode(output)

    assert "404 page not found" in summary
    assert failure_mode == "fatal"


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
