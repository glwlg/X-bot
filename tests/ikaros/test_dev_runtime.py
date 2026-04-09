import pytest

import ikaros.dev.runtime as runtime_module


def test_codex_default_template_enables_workspace_write(monkeypatch):
    monkeypatch.delenv("CODING_BACKEND_CODEX_ARGS_TEMPLATE", raising=False)
    cmd, args = runtime_module._build_coding_command("codex", "do something")
    assert cmd == "codex"
    assert "exec" in args
    assert "--model" in args
    model_index = args.index("--model")
    assert args[model_index + 1] == "gpt-5.3-codex"
    assert "-c" in args
    config_index = args.index("-c")
    assert args[config_index + 1] == "model_reasoning_effort=xhigh"
    assert "--sandbox" in args
    assert "workspace-write" in args


def test_gemini_default_template_sets_gemini_3_1_pro(monkeypatch):
    monkeypatch.delenv("CODING_BACKEND_GEMINI_ARGS_TEMPLATE", raising=False)
    cmd, args = runtime_module._build_coding_command("gemini-cli", "do something")
    assert cmd == "gemini-cli"
    assert "--model" in args
    model_index = args.index("--model")
    assert args[model_index + 1] == "gemini-3.1-pro"
    assert "--prompt" in args


def test_default_transport_uses_mixed_mode_when_unset(monkeypatch):
    monkeypatch.delenv("CODING_BACKEND_TRANSPORT_DEFAULT", raising=False)
    monkeypatch.delenv("CODING_BACKEND_CODEX_TRANSPORT", raising=False)
    monkeypatch.delenv("CODING_BACKEND_GEMINI_TRANSPORT", raising=False)
    monkeypatch.delenv("CODING_BACKEND_OPENCODE_TRANSPORT", raising=False)

    assert runtime_module._default_transport_for_backend("codex") == "cli"
    assert runtime_module._default_transport_for_backend("gemini-cli") == "acp"
    assert runtime_module._default_transport_for_backend("opencode") == "acp"


def test_codex_output_failure_detection_from_readonly_message():
    result = {
        "ok": True,
        "summary": "I couldn't create file because environment is mounted read-only",
        "stdout": "",
        "stderr": "",
    }
    assert runtime_module._codex_output_indicates_failure(result) is True

    patched = runtime_module._force_command_failed(result)
    assert patched["ok"] is False
    assert patched["error_code"] == "command_failed"


def test_subprocess_env_includes_persisted_gh_and_git_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    env = runtime_module._subprocess_env()

    assert env["GH_CONFIG_DIR"] == str(
        (tmp_path / "user" / "integrations" / "gh" / "config").resolve()
    )
    assert env["GIT_CONFIG_GLOBAL"] == str(
        (tmp_path / "user" / "integrations" / "git" / ".gitconfig").resolve()
    )
    assert env["GH_NO_UPDATE_NOTIFIER"] == "1"


@pytest.mark.asyncio
async def test_run_coding_backend_turns_zero_exit_readonly_into_failure(monkeypatch):
    async def fake_run_exec(command, *, cwd, timeout_sec=1200, log_path=""):
        _ = (command, cwd, timeout_sec, log_path)
        return {
            "ok": True,
            "error_code": "",
            "message": "",
            "summary": "I couldn't create TEST_WRITE.txt because this environment is mounted read-only.",
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(runtime_module, "run_exec", fake_run_exec)

    result = await runtime_module.run_coding_backend(
        instruction="create file",
        backend="codex",
        cwd="/tmp",
        timeout_sec=120,
        source="test",
    )

    assert result["ok"] is False
    assert result["error_code"] == "command_failed"


@pytest.mark.asyncio
async def test_run_coding_backend_routes_to_acp_transport(monkeypatch):
    captured = {}

    async def fake_run_acp_backend(
        *, command, cwd, instruction, timeout_sec, existing_session_id="", log_path="", env=None
    ):
        captured.update(
            {
                "command": command,
                "cwd": cwd,
                "instruction": instruction,
                "timeout_sec": timeout_sec,
                "existing_session_id": existing_session_id,
                "log_path": log_path,
                "env": dict(env or {}),
            }
        )
        return {
            "ok": True,
            "summary": "done",
            "stdout": "done",
            "transport_session_id": "acp-sess-1",
        }

    monkeypatch.setattr(runtime_module, "run_acp_backend", fake_run_acp_backend)

    result = await runtime_module.run_coding_backend(
        instruction="implement feature",
        backend="opencode",
        transport="acp",
        cwd="/tmp",
        timeout_sec=120,
        source="test",
        transport_session_id="acp-sess-prev",
    )

    assert result["ok"] is True
    assert result["backend"] == "opencode"
    assert result["transport"] == "acp"
    assert captured["command"][0] == "opencode"
    assert captured["cwd"] == "/tmp"
    assert captured["instruction"] == "implement feature"
    assert captured["existing_session_id"] == "acp-sess-prev"
    assert captured["env"]["OPENCODE_CLIENT"] == "ikaros"


@pytest.mark.asyncio
async def test_run_coding_backend_rejects_acp_for_codex():
    result = await runtime_module.run_coding_backend(
        instruction="implement feature",
        backend="codex",
        transport="acp",
        cwd="/tmp",
        timeout_sec=120,
        source="test",
    )

    assert result["ok"] is False
    assert result["error_code"] == "unsupported_transport"
