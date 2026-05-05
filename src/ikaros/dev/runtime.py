from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.state_paths import single_user_root
from ikaros.dev.acp_client import run_acp_backend
from ikaros.dev.codex_app_server_client import run_codex_app_server_backend


MAX_OUTPUT_CHARS = 12000
MAX_LOG_CHARS = 1_000_000


def _tail(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    payload = str(text or "")
    if len(payload) <= limit:
        return payload
    return payload[-limit:]


def _tail_for_log(text: str, limit: int = MAX_LOG_CHARS) -> str:
    payload = str(text or "")
    if len(payload) <= limit:
        return payload
    return payload[-limit:]


def _append_exec_log(
    *,
    log_path: str,
    command: List[str],
    cwd: str,
    timeout_sec: int,
    exit_code: int,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> None:
    safe_log_path = str(log_path or "").strip()
    if not safe_log_path:
        return

    try:
        target = Path(safe_log_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        lines = [
            f"[{stamp}] command={_command_to_text(command)} cwd={cwd}",
            f"timeout_sec={int(timeout_sec or 0)} timed_out={str(bool(timed_out)).lower()} exit_code={int(exit_code)}",
            "--- stdout ---",
            _tail_for_log(stdout),
            "--- stderr ---",
            _tail_for_log(stderr),
            "--- end ---",
            "",
        ]
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except Exception:
        return


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_backend(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if token in {"gemini", "gemini_cli", "gemini-cli"}:
        return "gemini-cli"
    if token in {"opencode", "open-code"}:
        return "opencode"
    if token in {"codex", "openai-codex", ""}:
        return "codex"
    return "codex"


def _normalize_transport(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if token in {"acp", "agent-client-protocol"}:
        return "acp"
    if token in {
        "app-server",
        "app_server",
        "appserver",
        "codex-app-server",
        "codex_app_server",
    }:
        return "app-server"
    return "cli"


def _backend_env_key(backend: str) -> str:
    safe_backend = _normalize_backend(backend)
    if safe_backend == "gemini-cli":
        return "GEMINI"
    if safe_backend == "opencode":
        return "OPENCODE"
    return "CODEX"


def _default_transport_for_backend(backend: str) -> str:
    env_key = _backend_env_key(backend)
    specific = str(os.getenv(f"CODING_BACKEND_{env_key}_TRANSPORT", "") or "").strip()
    if specific:
        return _normalize_transport(specific)
    default = str(os.getenv("CODING_BACKEND_TRANSPORT_DEFAULT", "") or "").strip()
    if default:
        return _normalize_transport(default)
    if _normalize_backend(backend) == "codex":
        return "app-server"
    if _normalize_backend(backend) in {"gemini-cli", "opencode"}:
        return "acp"
    return "cli"


def _build_coding_command(backend: str, instruction: str) -> tuple[str, List[str]]:
    safe_backend = _normalize_backend(backend)
    safe_instruction = str(instruction or "").strip()

    if safe_backend == "gemini-cli":
        cmd = str(
            os.getenv("CODING_BACKEND_GEMINI_COMMAND", "gemini-cli") or ""
        ).strip()
        template = str(
            os.getenv(
                "CODING_BACKEND_GEMINI_ARGS_TEMPLATE",
                "--model gemini-3.1-pro --prompt {instruction}",
            )
            or ""
        ).strip()
    else:
        cmd = str(os.getenv("CODING_BACKEND_CODEX_COMMAND", "codex") or "").strip()
        template = str(
            os.getenv(
                "CODING_BACKEND_CODEX_ARGS_TEMPLATE",
                (
                    "exec --model gpt-5.3-codex "
                    '-c model_reasoning_effort="xhigh" '
                    "--sandbox workspace-write {instruction}"
                ),
            )
            or ""
        ).strip()

    rendered = template.format(instruction=shlex.quote(safe_instruction))
    args = shlex.split(rendered)
    return cmd, args


def _build_acp_command(
    backend: str,
    *,
    cwd: str,
) -> tuple[str, List[str], Dict[str, str]]:
    safe_backend = _normalize_backend(backend)
    safe_cwd = str(cwd or "").strip()
    if safe_backend == "opencode":
        cmd = str(os.getenv("CODING_BACKEND_OPENCODE_ACP_COMMAND", "opencode") or "").strip()
        template = str(
            os.getenv(
                "CODING_BACKEND_OPENCODE_ACP_ARGS_TEMPLATE",
                "acp --cwd {cwd}",
            )
            or ""
        ).strip()
        env_overrides = {
            "OPENCODE_CLIENT": "ikaros",
            "OPENCODE_DISABLE_MODELS_FETCH": str(
                os.getenv("OPENCODE_DISABLE_MODELS_FETCH", "1") or "1"
            ).strip(),
        }
    elif safe_backend == "gemini-cli":
        cmd = str(
            os.getenv(
                "CODING_BACKEND_GEMINI_ACP_COMMAND",
                os.getenv("CODING_BACKEND_GEMINI_COMMAND", "gemini"),
            )
            or ""
        ).strip()
        template = str(
            os.getenv(
                "CODING_BACKEND_GEMINI_ACP_ARGS_TEMPLATE",
                "--experimental-acp",
            )
            or ""
        ).strip()
        env_overrides = {}
    else:
        raise ValueError(f"ACP transport is not supported for backend: {safe_backend}")

    rendered = template.format(cwd=shlex.quote(safe_cwd))
    args = shlex.split(rendered)
    return cmd, args, env_overrides


def _build_codex_app_server_command(*, cwd: str) -> tuple[str, List[str]]:
    safe_cwd = str(cwd or "").strip()
    cmd = str(
        os.getenv(
            "CODING_BACKEND_CODEX_APP_SERVER_COMMAND",
            os.getenv("CODING_BACKEND_CODEX_COMMAND", "codex"),
        )
        or ""
    ).strip()
    template = str(
        os.getenv(
            "CODING_BACKEND_CODEX_APP_SERVER_ARGS_TEMPLATE",
            "app-server --listen stdio://",
        )
        or ""
    ).strip()
    rendered = template.format(cwd=shlex.quote(safe_cwd))
    args = shlex.split(rendered)
    return cmd, args


def _codex_app_server_model() -> str:
    return str(
        os.getenv(
            "CODING_BACKEND_CODEX_APP_SERVER_MODEL",
            os.getenv("CODING_BACKEND_CODEX_MODEL", "gpt-5.3-codex"),
        )
        or ""
    ).strip()


def _codex_app_server_effort() -> str:
    return str(
        os.getenv(
            "CODING_BACKEND_CODEX_APP_SERVER_EFFORT",
            os.getenv("CODING_BACKEND_CODEX_REASONING_EFFORT", "xhigh"),
        )
        or ""
    ).strip()


def _codex_app_server_approval_policy() -> str:
    return str(
        os.getenv("CODING_BACKEND_CODEX_APP_SERVER_APPROVAL_POLICY", "never") or ""
    ).strip()


def _codex_app_server_sandbox() -> str:
    return str(
        os.getenv("CODING_BACKEND_CODEX_APP_SERVER_SANDBOX", "workspace-write") or ""
    ).strip()


def _codex_app_server_approval_decision() -> str:
    return str(
        os.getenv("CODING_BACKEND_CODEX_APP_SERVER_APPROVAL_DECISION", "accept") or ""
    ).strip()


def _is_codex_trust_error(text: str) -> bool:
    payload = str(text or "").lower()
    return (
        "not inside a trusted directory" in payload
        and "--skip-git-repo-check" in payload
    )


def _inject_skip_git_repo_check(args: List[str]) -> List[str]:
    if "--skip-git-repo-check" in args:
        return list(args)
    patched = list(args)
    if "exec" in patched:
        idx = patched.index("exec")
        patched.insert(idx + 1, "--skip-git-repo-check")
        return patched
    patched.append("--skip-git-repo-check")
    return patched


def _codex_output_indicates_failure(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict) or not bool(result.get("ok")):
        return False
    combined = "\n".join(
        [
            str(result.get("summary") or ""),
            str(result.get("stderr") or ""),
            str(result.get("stdout") or ""),
        ]
    ).lower()
    failure_markers = (
        "permission denied",
        "read-only",
        "mounted read-only",
        "operation not permitted",
        "couldn't create",
        "could not create",
        "cannot create",
        "failed to create",
        "failed to write",
    )
    return any(marker in combined for marker in failure_markers)


def _force_command_failed(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(result or {})
    summary = str(
        payload.get("summary") or payload.get("stderr") or payload.get("stdout") or ""
    ).strip()
    if not summary:
        summary = "codex reported failure despite zero exit code"
    payload["ok"] = False
    payload["error_code"] = "command_failed"
    payload["message"] = summary
    payload["summary"] = summary
    return payload


def _codex_app_server_unavailable(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("error_code") or "").strip() == "command_not_found":
        return True
    combined = "\n".join(
        [
            str(result.get("message") or ""),
            str(result.get("summary") or ""),
            str(result.get("stderr") or ""),
            str(result.get("stdout") or ""),
        ]
    ).lower()
    markers = (
        "unrecognized subcommand",
        "invalid subcommand",
        "unexpected argument 'app-server'",
        "unexpected argument \"app-server\"",
        "unknown command app-server",
        "unknown subcommand app-server",
    )
    return any(marker in combined for marker in markers)


def _command_to_text(command: List[str]) -> str:
    return " ".join([shlex.quote(part) for part in command])


def _subprocess_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GCM_INTERACTIVE", "never")
    env.setdefault("CI", "true")
    env.setdefault("PAGER", "cat")
    env.setdefault("GIT_PAGER", "cat")
    env.setdefault(
        "GH_CONFIG_DIR",
        str((single_user_root() / "integrations" / "gh" / "config").resolve()),
    )
    env.setdefault(
        "GIT_CONFIG_GLOBAL",
        str((single_user_root() / "integrations" / "git" / ".gitconfig").resolve()),
    )
    env.setdefault("GH_NO_UPDATE_NOTIFIER", "1")
    return env


async def run_exec(
    command: List[str],
    *,
    cwd: str,
    timeout_sec: int = 1200,
    log_path: str = "",
) -> Dict[str, Any]:
    safe_command = [str(item) for item in list(command or []) if str(item)]
    if not safe_command:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "command is required",
        }
    safe_cwd = str(cwd or "").strip()
    if not safe_cwd:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "cwd is required",
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            *safe_command,
            cwd=safe_cwd,
            env=_subprocess_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error_code": "command_not_found",
            "message": f"command not found: {safe_command[0]}",
            "command": _command_to_text(safe_command),
            "cwd": safe_cwd,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "exec_prepare_failed",
            "message": str(exc),
            "command": _command_to_text(safe_command),
            "cwd": safe_cwd,
        }

    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            proc.communicate(), timeout=max(1, int(timeout_sec or 1200))
        )
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        stdout_raw, stderr_raw = await proc.communicate()
        stdout_full = stdout_raw.decode("utf-8", errors="replace")
        stderr_full = stderr_raw.decode("utf-8", errors="replace")
        _append_exec_log(
            log_path=log_path,
            command=safe_command,
            cwd=safe_cwd,
            timeout_sec=int(timeout_sec or 0),
            exit_code=-1,
            stdout=stdout_full,
            stderr=stderr_full,
            timed_out=True,
        )
        stdout = _tail(stdout_full)
        stderr = _tail(stderr_full)
        return {
            "ok": False,
            "error_code": "timeout",
            "message": f"command timed out after {timeout_sec}s",
            "command": _command_to_text(safe_command),
            "cwd": safe_cwd,
            "exit_code": -1,
            "stdout": stdout,
            "stderr": stderr,
            "summary": _tail(stderr or stdout),
            "log_path": str(log_path or "").strip(),
        }

    stdout_full = stdout_raw.decode("utf-8", errors="replace")
    stderr_full = stderr_raw.decode("utf-8", errors="replace")
    _append_exec_log(
        log_path=log_path,
        command=safe_command,
        cwd=safe_cwd,
        timeout_sec=int(timeout_sec or 0),
        exit_code=int(proc.returncode or 0),
        stdout=stdout_full,
        stderr=stderr_full,
        timed_out=False,
    )
    stdout = _tail(stdout_full)
    stderr = _tail(stderr_full)
    summary = _tail((stderr or stdout).strip())
    return {
        "ok": int(proc.returncode or 0) == 0,
        "error_code": "" if int(proc.returncode or 0) == 0 else "command_failed",
        "message": "" if int(proc.returncode or 0) == 0 else summary,
        "command": _command_to_text(safe_command),
        "cwd": safe_cwd,
        "exit_code": int(proc.returncode or 0),
        "stdout": stdout,
        "stderr": stderr,
        "summary": summary,
        "log_path": str(log_path or "").strip(),
    }


async def run_shell(
    command: str,
    *,
    cwd: str,
    timeout_sec: int = 1200,
) -> Dict[str, Any]:
    safe_command = str(command or "").strip()
    if not safe_command:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "command is required",
        }
    safe_cwd = str(cwd or "").strip()
    if not safe_cwd:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "cwd is required",
        }

    try:
        proc = await asyncio.create_subprocess_shell(
            safe_command,
            cwd=safe_cwd,
            env=_subprocess_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "exec_prepare_failed",
            "message": str(exc),
            "command": safe_command,
            "cwd": safe_cwd,
        }

    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            proc.communicate(), timeout=max(1, int(timeout_sec or 1200))
        )
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        stdout_raw, stderr_raw = await proc.communicate()
        stdout = _tail(stdout_raw.decode("utf-8", errors="replace"))
        stderr = _tail(stderr_raw.decode("utf-8", errors="replace"))
        return {
            "ok": False,
            "error_code": "timeout",
            "message": f"command timed out after {timeout_sec}s",
            "command": safe_command,
            "cwd": safe_cwd,
            "exit_code": -1,
            "stdout": stdout,
            "stderr": stderr,
            "summary": _tail(stderr or stdout),
        }

    stdout = _tail(stdout_raw.decode("utf-8", errors="replace"))
    stderr = _tail(stderr_raw.decode("utf-8", errors="replace"))
    summary = _tail((stderr or stdout).strip())
    return {
        "ok": int(proc.returncode or 0) == 0,
        "error_code": "" if int(proc.returncode or 0) == 0 else "command_failed",
        "message": "" if int(proc.returncode or 0) == 0 else summary,
        "command": safe_command,
        "cwd": safe_cwd,
        "exit_code": int(proc.returncode or 0),
        "stdout": stdout,
        "stderr": stderr,
        "summary": summary,
    }


async def run_coding_backend(
    *,
    instruction: str,
    backend: str,
    cwd: str,
    timeout_sec: int = 1800,
    source: str = "",
    log_path: str = "",
    transport: str = "",
    transport_session_id: str = "",
) -> Dict[str, Any]:
    safe_instruction = str(instruction or "").strip()
    if not safe_instruction:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "instruction is required",
        }

    configured_backend = backend or os.getenv("CODING_BACKEND_DEFAULT") or "codex"
    backend_name = _normalize_backend(configured_backend)
    explicit_transport = bool(str(transport or "").strip())
    transport_name = _normalize_transport(
        transport or _default_transport_for_backend(backend_name)
    )
    if transport_name == "acp":
        try:
            cmd, args, env_overrides = _build_acp_command(
                backend_name,
                cwd=str(cwd or "").strip(),
            )
        except ValueError as exc:
            return {
                "ok": False,
                "error_code": "unsupported_transport",
                "message": str(exc),
                "backend": backend_name,
                "transport": "acp",
                "source": str(source or "").strip(),
            }
        env = _subprocess_env()
        env.update(env_overrides)
        result = await run_acp_backend(
            command=[cmd, *args],
            cwd=str(cwd or "").strip(),
            instruction=safe_instruction,
            timeout_sec=max(60, int(timeout_sec or 1800)),
            existing_session_id=str(transport_session_id or "").strip(),
            log_path=log_path,
            env=env,
        )
        result["backend"] = backend_name
        result["transport"] = "acp"
        result["source"] = str(source or "").strip()
        return result

    app_server_error: Dict[str, Any] | None = None
    if transport_name == "app-server":
        if backend_name != "codex":
            return {
                "ok": False,
                "error_code": "unsupported_transport",
                "message": (
                    f"app-server transport is not supported for backend: {backend_name}"
                ),
                "backend": backend_name,
                "transport": "app-server",
                "source": str(source or "").strip(),
            }
        cmd, args = _build_codex_app_server_command(cwd=str(cwd or "").strip())
        result = await run_codex_app_server_backend(
            command=[cmd, *args],
            cwd=str(cwd or "").strip(),
            instruction=safe_instruction,
            timeout_sec=max(60, int(timeout_sec or 1800)),
            existing_thread_id=str(transport_session_id or "").strip(),
            log_path=log_path,
            env=_subprocess_env(),
            model=_codex_app_server_model(),
            effort=_codex_app_server_effort(),
            approval_policy=_codex_app_server_approval_policy(),
            sandbox=_codex_app_server_sandbox(),
            approval_decision=_codex_app_server_approval_decision(),
        )
        result["backend"] = backend_name
        result["transport"] = "app-server"
        result["source"] = str(source or "").strip()
        fallback_enabled = _as_bool(
            os.getenv("CODING_BACKEND_CODEX_APP_SERVER_FALLBACK_TO_CLI", "true"),
            default=True,
        )
        if (
            explicit_transport
            or bool(result.get("ok"))
            or not fallback_enabled
            or not _codex_app_server_unavailable(result)
        ):
            return result
        app_server_error = dict(result)

    cmd, args = _build_coding_command(backend_name, safe_instruction)
    first = await run_exec(
        [cmd, *args],
        cwd=str(cwd or "").strip(),
        timeout_sec=max(60, int(timeout_sec or 1800)),
        log_path=log_path,
    )
    first["backend"] = backend_name
    first["transport"] = "cli"
    first["source"] = str(source or "").strip()
    if app_server_error is not None:
        first["fallback_from_transport"] = "app-server"
        first["app_server_error"] = app_server_error
    if backend_name == "codex" and _codex_output_indicates_failure(first):
        first = _force_command_failed(first)

    auto_skip = _as_bool(
        os.getenv("CODING_BACKEND_CODEX_AUTO_SKIP_GIT_REPO_CHECK", "true"),
        default=True,
    )
    if backend_name != "codex" or not auto_skip or bool(first.get("ok")):
        return first

    combined = "\n".join(
        [
            str(first.get("summary") or ""),
            str(first.get("stderr") or ""),
            str(first.get("stdout") or ""),
        ]
    )
    if not _is_codex_trust_error(combined):
        return first

    retry_args = _inject_skip_git_repo_check(args)
    second = await run_exec(
        [cmd, *retry_args],
        cwd=str(cwd or "").strip(),
        timeout_sec=max(60, int(timeout_sec or 1800)),
        log_path=log_path,
    )
    second["backend"] = backend_name
    second["transport"] = "cli"
    second["source"] = str(source or "").strip()
    second["retry_hint"] = "skip_git_repo_check"
    if app_server_error is not None:
        second["fallback_from_transport"] = "app-server"
        second["app_server_error"] = app_server_error
    if backend_name == "codex" and _codex_output_indicates_failure(second):
        second = _force_command_failed(second)
    return second
