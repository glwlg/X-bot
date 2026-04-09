from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4


MAX_OUTPUT_CHARS = 12000
MAX_LOG_CHARS = 1_000_000
DEFAULT_TERMINAL_OUTPUT_BYTES = 65536


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def _trim_output_bytes(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0:
        return "", bool(text)
    encoded = str(text or "").encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return str(text or ""), False
    trimmed = encoded[-limit:]
    while trimmed:
        try:
            return trimmed.decode("utf-8"), True
        except UnicodeDecodeError as exc:
            trimmed = trimmed[exc.start + 1 :]
    return "", True


def _command_to_text(command: List[str]) -> str:
    return " ".join([json.dumps(str(part)) for part in list(command or []) if part])


def _append_acp_log(
    *,
    log_path: str,
    command: List[str],
    cwd: str,
    session_id: str,
    stop_reason: str,
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
        lines = [
            f"[{_now_iso()}] transport=acp command={_command_to_text(command)} cwd={cwd}",
            f"session_id={session_id} timed_out={str(bool(timed_out)).lower()} stop_reason={stop_reason}",
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


def _resolve_workspace_path(
    *,
    workspace_root: Path,
    raw_path: str,
    allow_missing: bool = False,
) -> Path:
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"path must be absolute: {raw_path}")
    resolved = candidate.resolve(strict=False)
    root = workspace_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {raw_path}") from exc
    if not allow_missing and not resolved.exists():
        raise FileNotFoundError(str(resolved))
    return resolved


def _select_permission_outcome(options: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not options:
        return {"outcome": "cancelled"}
    preferred_kinds = ("allow_once", "allow_always", "reject_once", "reject_always")
    chosen: Dict[str, Any] | None = None
    for kind in preferred_kinds:
        chosen = next(
            (item for item in options if str(item.get("kind") or "").strip() == kind),
            None,
        )
        if chosen is not None:
            break
    if chosen is None:
        chosen = dict(options[0])
    option_id = str(chosen.get("optionId") or "").strip()
    if not option_id:
        return {"outcome": "cancelled"}
    return {"outcome": "selected", "optionId": option_id}


def _render_content_block(block: Dict[str, Any]) -> str:
    payload = dict(block or {})
    block_type = str(payload.get("type") or "").strip()
    if block_type == "text":
        return str(payload.get("text") or "")
    if block_type == "resource":
        resource = payload.get("resource")
        if isinstance(resource, dict):
            return str(resource.get("text") or "")
    if block_type == "content":
        inner = payload.get("content")
        if isinstance(inner, dict):
            return _render_content_block(inner)
    if block_type == "diff":
        path = str(payload.get("path") or "").strip()
        return path
    return ""


def _normalize_env_list(value: Any) -> Dict[str, str]:
    rows: Dict[str, str] = {}
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        rows[name] = str(item.get("value") or "")
    return rows


def _terminal_exit_status(return_code: int | None) -> Dict[str, Any]:
    if return_code is None:
        return {"exitCode": None, "signal": None}
    if return_code >= 0:
        return {"exitCode": int(return_code), "signal": None}
    return {"exitCode": None, "signal": str(abs(int(return_code)))}


class JsonRpcError(RuntimeError):
    def __init__(self, *, code: int, message: str, data: Any = None) -> None:
        super().__init__(str(message or "json-rpc error"))
        self.code = int(code)
        self.message = str(message or "json-rpc error")
        self.data = data


@dataclass
class TerminalSession:
    terminal_id: str
    process: asyncio.subprocess.Process
    output_limit: int = DEFAULT_TERMINAL_OUTPUT_BYTES
    output: str = ""
    truncated: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _readers: List[asyncio.Task[Any]] = field(default_factory=list)

    async def start(self) -> None:
        self._readers = [
            asyncio.create_task(self._pump(self.process.stdout)),
            asyncio.create_task(self._pump(self.process.stderr)),
        ]

    async def _pump(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace")
            async with self._lock:
                merged = self.output + text
                self.output, was_truncated = _trim_output_bytes(
                    merged,
                    max(0, int(self.output_limit or 0)),
                )
                self.truncated = self.truncated or was_truncated

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            status = (
                _terminal_exit_status(self.process.returncode)
                if self.process.returncode is not None
                else None
            )
            return {
                "output": self.output,
                "truncated": bool(self.truncated),
                "exitStatus": status,
            }

    async def wait_for_exit(self) -> Dict[str, Any]:
        return_code = await self.process.wait()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._readers, return_exceptions=True)
        return _terminal_exit_status(return_code)

    async def kill(self) -> None:
        if self.process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            self.process.kill()
        with contextlib.suppress(Exception):
            await self.process.wait()

    async def release(self) -> None:
        await self.kill()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._readers, return_exceptions=True)


class StdioAcpClient:
    def __init__(
        self,
        *,
        command: List[str],
        cwd: str,
        env: Dict[str, str],
        timeout_sec: int,
        log_path: str = "",
    ) -> None:
        self.command = [str(item) for item in list(command or []) if str(item)]
        self.cwd = str(cwd or "").strip()
        self.env = dict(env or {})
        self.timeout_sec = max(30, int(timeout_sec or 0))
        self.log_path = str(log_path or "").strip()
        self.workspace_root = Path(self.cwd).resolve()
        self.proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[Any] | None = None
        self._stderr_task: asyncio.Task[Any] | None = None
        self._request_id = 0
        self._pending: Dict[int, asyncio.Future[Any]] = {}
        self._terminals: Dict[str, TerminalSession] = {}
        self.stderr_text = ""
        self.protocol_version = 1
        self.agent_capabilities: Dict[str, Any] = {}
        self.agent_info: Dict[str, Any] = {}
        self.session_updates: List[Dict[str, Any]] = []
        self.tool_calls: Dict[str, Dict[str, Any]] = {}
        self.plan: Dict[str, Any] = {}
        self.available_commands: List[Dict[str, Any]] = []
        self.session_info: Dict[str, Any] = {}
        self.assistant_chunks: List[str] = []
        self.thought_chunks: List[str] = []
        self.permission_requests: List[Dict[str, Any]] = []

    async def start(self) -> None:
        if not self.command:
            raise ValueError("ACP command is required")
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def close(self) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()
        for terminal in list(self._terminals.values()):
            with contextlib.suppress(Exception):
                await terminal.release()
        self._terminals.clear()
        if self.proc is None:
            return
        if self.proc.stdin is not None:
            with contextlib.suppress(Exception):
                self.proc.stdin.close()
        if self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                with contextlib.suppress(Exception):
                    await self.proc.wait()
        if self._reader_task is not None:
            with contextlib.suppress(Exception):
                await self._reader_task
        if self._stderr_task is not None:
            with contextlib.suppress(Exception):
                await self._stderr_task

    async def initialize(self) -> Dict[str, Any]:
        response = await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "ikaros", "version": "0.2.1"},
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
        )
        self.protocol_version = int(response.get("protocolVersion") or 1)
        self.agent_capabilities = dict(response.get("agentCapabilities") or {})
        self.agent_info = dict(response.get("agentInfo") or {})
        return response

    async def open_session(self, *, existing_session_id: str = "") -> tuple[str, bool]:
        safe_existing = str(existing_session_id or "").strip()
        load_supported = bool(self.agent_capabilities.get("loadSession"))
        if safe_existing and load_supported:
            try:
                await self.request(
                    "session/load",
                    {
                        "sessionId": safe_existing,
                        "cwd": self.cwd,
                        "mcpServers": [],
                    },
                )
                return safe_existing, True
            except JsonRpcError:
                pass
        created = await self.request(
            "session/new",
            {
                "cwd": self.cwd,
                "mcpServers": [],
            },
        )
        session_id = str(created.get("sessionId") or "").strip()
        if not session_id:
            raise RuntimeError("ACP agent did not return a session id")
        return session_id, False

    async def prompt(self, *, session_id: str, instruction: str) -> Dict[str, Any]:
        return await self.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [
                    {
                        "type": "text",
                        "text": str(instruction or "").strip(),
                    }
                ],
            },
        )

    async def notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._send_json(
            {
                "jsonrpc": "2.0",
                "method": str(method),
                "params": dict(params or {}),
            }
        )

    async def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        await self._send_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": str(method),
                "params": dict(params or {}),
            }
        )
        try:
            result = await asyncio.wait_for(future, timeout=self.timeout_sec)
        finally:
            self._pending.pop(request_id, None)
        if isinstance(result, Exception):
            raise result
        return dict(result or {})

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("ACP process is not running")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line.encode("utf-8"))
        await self.proc.stdin.drain()

    async def _send_response(
        self,
        request_id: Any,
        *,
        result: Dict[str, Any] | None = None,
        error: Dict[str, Any] | None = None,
    ) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = dict(result or {})
        await self._send_json(payload)

    async def _read_stdout(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                self.stderr_text = _tail(self.stderr_text + "\n" + raw)
                continue
            if isinstance(payload, dict):
                await self._handle_payload(payload)

    async def _read_stderr(self) -> None:
        if self.proc is None or self.proc.stderr is None:
            return
        while True:
            chunk = await self.proc.stderr.read(4096)
            if not chunk:
                return
            self.stderr_text = _tail(
                self.stderr_text + chunk.decode("utf-8", errors="replace"),
                MAX_LOG_CHARS,
            )

    async def _handle_payload(self, payload: Dict[str, Any]) -> None:
        if "method" in payload:
            method = str(payload.get("method") or "").strip()
            request_id = payload.get("id")
            params = payload.get("params")
            safe_params = dict(params) if isinstance(params, dict) else {}
            if request_id is None:
                self._handle_notification(method, safe_params)
                return
            await self._handle_request(request_id, method, safe_params)
            return
        request_id = payload.get("id")
        if request_id is None:
            return
        future = self._pending.get(int(request_id))
        if future is None or future.done():
            return
        if "error" in payload and isinstance(payload.get("error"), dict):
            error = dict(payload.get("error") or {})
            future.set_result(
                JsonRpcError(
                    code=int(error.get("code") or -32603),
                    message=str(error.get("message") or "json-rpc error"),
                    data=error.get("data"),
                )
            )
            return
        future.set_result(dict(payload.get("result") or {}))

    def _handle_notification(self, method: str, params: Dict[str, Any]) -> None:
        if method != "session/update":
            return
        update = params.get("update")
        safe_update = dict(update) if isinstance(update, dict) else {}
        update_type = str(safe_update.get("sessionUpdate") or "").strip()
        if safe_update:
            self.session_updates.append(safe_update)
            self.session_updates = self.session_updates[-100:]
        if update_type == "agent_message_chunk":
            content = safe_update.get("content")
            if isinstance(content, dict):
                text = _render_content_block(content)
                if text:
                    self.assistant_chunks.append(text)
        elif update_type == "agent_thought_chunk":
            content = safe_update.get("content")
            if isinstance(content, dict):
                text = _render_content_block(content)
                if text:
                    self.thought_chunks.append(text)
        elif update_type == "tool_call":
            tool_call_id = str(safe_update.get("toolCallId") or "").strip()
            if tool_call_id:
                self.tool_calls[tool_call_id] = dict(safe_update)
        elif update_type == "tool_call_update":
            tool_call_id = str(safe_update.get("toolCallId") or "").strip()
            if tool_call_id:
                current = dict(self.tool_calls.get(tool_call_id) or {})
                current.update(
                    {
                        key: value
                        for key, value in safe_update.items()
                        if value is not None
                    }
                )
                self.tool_calls[tool_call_id] = current
        elif update_type == "plan":
            self.plan = dict(safe_update)
        elif update_type == "available_commands_update":
            self.available_commands = list(safe_update.get("availableCommands") or [])
        elif update_type == "session_info_update":
            self.session_info = dict(safe_update)

    async def _handle_request(
        self,
        request_id: Any,
        method: str,
        params: Dict[str, Any],
    ) -> None:
        try:
            if method == "fs/read_text_file":
                path = _resolve_workspace_path(
                    workspace_root=self.workspace_root,
                    raw_path=str(params.get("path") or ""),
                    allow_missing=False,
                )
                content = path.read_text(encoding="utf-8")
                await self._send_response(request_id, result={"content": content})
                return

            if method == "fs/write_text_file":
                path = _resolve_workspace_path(
                    workspace_root=self.workspace_root,
                    raw_path=str(params.get("path") or ""),
                    allow_missing=True,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(params.get("content") or ""), encoding="utf-8")
                await self._send_response(request_id, result={})
                return

            if method == "session/request_permission":
                options = [
                    dict(item)
                    for item in list(params.get("options") or [])
                    if isinstance(item, dict)
                ]
                self.permission_requests.append(
                    {
                        "at": _now_iso(),
                        "tool_call": dict(params.get("toolCall") or {}),
                        "options": options,
                    }
                )
                await self._send_response(
                    request_id,
                    result={"outcome": _select_permission_outcome(options)},
                )
                return

            if method == "terminal/create":
                terminal_id = f"term-{uuid4().hex[:12]}"
                raw_cwd = str(params.get("cwd") or self.cwd).strip() or self.cwd
                terminal_cwd = _resolve_workspace_path(
                    workspace_root=self.workspace_root,
                    raw_path=raw_cwd,
                    allow_missing=False,
                )
                terminal_env = dict(self.env)
                terminal_env.update(_normalize_env_list(params.get("env") or []))
                process = await asyncio.create_subprocess_exec(
                    str(params.get("command") or ""),
                    *[
                        str(item)
                        for item in list(params.get("args") or [])
                        if str(item) != ""
                    ],
                    cwd=str(terminal_cwd),
                    env=terminal_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                terminal = TerminalSession(
                    terminal_id=terminal_id,
                    process=process,
                    output_limit=max(
                        0,
                        int(
                            params.get("outputByteLimit")
                            or DEFAULT_TERMINAL_OUTPUT_BYTES
                        ),
                    ),
                )
                await terminal.start()
                self._terminals[terminal_id] = terminal
                await self._send_response(
                    request_id,
                    result={"terminalId": terminal_id},
                )
                return

            if method == "terminal/output":
                terminal_id = str(params.get("terminalId") or "").strip()
                terminal = self._terminals.get(terminal_id)
                if terminal is None:
                    raise FileNotFoundError(f"terminal not found: {terminal_id}")
                await self._send_response(request_id, result=await terminal.snapshot())
                return

            if method == "terminal/wait_for_exit":
                terminal_id = str(params.get("terminalId") or "").strip()
                terminal = self._terminals.get(terminal_id)
                if terminal is None:
                    raise FileNotFoundError(f"terminal not found: {terminal_id}")
                await self._send_response(
                    request_id,
                    result=await terminal.wait_for_exit(),
                )
                return

            if method == "terminal/kill":
                terminal_id = str(params.get("terminalId") or "").strip()
                terminal = self._terminals.get(terminal_id)
                if terminal is None:
                    raise FileNotFoundError(f"terminal not found: {terminal_id}")
                await terminal.kill()
                await self._send_response(request_id, result={})
                return

            if method == "terminal/release":
                terminal_id = str(params.get("terminalId") or "").strip()
                terminal = self._terminals.pop(terminal_id, None)
                if terminal is not None:
                    await terminal.release()
                await self._send_response(request_id, result={})
                return

            await self._send_response(
                request_id,
                error={"code": -32601, "message": f"unsupported ACP method: {method}"},
            )
        except FileNotFoundError as exc:
            await self._send_response(
                request_id,
                error={"code": -32002, "message": str(exc)},
            )
        except ValueError as exc:
            await self._send_response(
                request_id,
                error={"code": -32602, "message": str(exc)},
            )
        except Exception as exc:
            await self._send_response(
                request_id,
                error={"code": -32603, "message": str(exc)},
            )

    def build_result(
        self,
        *,
        session_id: str,
        prompt_result: Dict[str, Any],
        loaded_existing_session: bool,
    ) -> Dict[str, Any]:
        stdout = "".join(self.assistant_chunks).strip()
        thought = "".join(self.thought_chunks).strip()
        stop_reason = str(prompt_result.get("stopReason") or "").strip()
        ok = stop_reason in {"end_turn", "max_tokens", "max_turn_requests"}
        if stop_reason == "refusal":
            ok = False
        summary = _tail(stdout or stop_reason or "ACP round completed")
        return {
            "ok": ok,
            "error_code": "" if ok else "command_failed",
            "message": "" if ok else summary,
            "command": _command_to_text(self.command),
            "cwd": self.cwd,
            "exit_code": 0 if ok else 1,
            "stdout": stdout,
            "stderr": _tail(self.stderr_text),
            "summary": summary,
            "backend": "",
            "transport": "acp",
            "stop_reason": stop_reason,
            "transport_session_id": session_id,
            "loaded_existing_session": bool(loaded_existing_session),
            "agent_info": dict(self.agent_info or {}),
            "agent_capabilities": dict(self.agent_capabilities or {}),
            "protocol_version": int(self.protocol_version or 1),
            "plan": dict(self.plan or {}),
            "thought": _tail(thought, MAX_LOG_CHARS),
            "tool_calls": list(self.tool_calls.values()),
            "available_commands": list(self.available_commands or []),
            "session_info": dict(self.session_info or {}),
            "permission_requests": list(self.permission_requests or []),
            "session_updates": list(self.session_updates or []),
            "log_path": self.log_path,
        }


async def run_acp_backend(
    *,
    command: List[str],
    cwd: str,
    instruction: str,
    timeout_sec: int = 1800,
    existing_session_id: str = "",
    log_path: str = "",
    env: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    safe_instruction = str(instruction or "").strip()
    safe_cwd = str(cwd or "").strip()
    safe_command = [str(item) for item in list(command or []) if str(item)]
    if not safe_instruction:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "instruction is required",
        }
    if not safe_cwd:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "cwd is required",
        }
    if not safe_command:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "ACP command is required",
        }

    client = StdioAcpClient(
        command=safe_command,
        cwd=safe_cwd,
        env=dict(env or os.environ),
        timeout_sec=max(30, int(timeout_sec or 0)),
        log_path=log_path,
    )
    session_id = ""
    try:
        await client.start()
        await client.initialize()
        session_id, loaded_existing = await client.open_session(
            existing_session_id=existing_session_id
        )
        prompt_result = await asyncio.wait_for(
            client.prompt(session_id=session_id, instruction=safe_instruction),
            timeout=max(30, int(timeout_sec or 0)),
        )
        result = client.build_result(
            session_id=session_id,
            prompt_result=prompt_result,
            loaded_existing_session=loaded_existing,
        )
        _append_acp_log(
            log_path=log_path,
            command=safe_command,
            cwd=safe_cwd,
            session_id=session_id,
            stop_reason=str(result.get("stop_reason") or ""),
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            timed_out=False,
        )
        return result
    except FileNotFoundError:
        return {
            "ok": False,
            "error_code": "command_not_found",
            "message": f"command not found: {safe_command[0]}",
            "command": _command_to_text(safe_command),
            "cwd": safe_cwd,
            "transport": "acp",
            "transport_session_id": session_id,
            "log_path": log_path,
        }
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            if session_id:
                await client.notify("session/cancel", {"sessionId": session_id})
        result = {
            "ok": False,
            "error_code": "timeout",
            "message": f"ACP round timed out after {timeout_sec}s",
            "command": _command_to_text(safe_command),
            "cwd": safe_cwd,
            "stdout": "".join(client.assistant_chunks).strip(),
            "stderr": _tail(client.stderr_text),
            "summary": _tail("".join(client.assistant_chunks).strip() or client.stderr_text),
            "transport": "acp",
            "transport_session_id": session_id,
            "log_path": log_path,
        }
        _append_acp_log(
            log_path=log_path,
            command=safe_command,
            cwd=safe_cwd,
            session_id=session_id,
            stop_reason="timeout",
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            timed_out=True,
        )
        return result
    except JsonRpcError as exc:
        return {
            "ok": False,
            "error_code": "command_failed",
            "message": exc.message,
            "command": _command_to_text(safe_command),
            "cwd": safe_cwd,
            "stdout": "".join(client.assistant_chunks).strip(),
            "stderr": _tail(client.stderr_text),
            "summary": _tail(exc.message),
            "transport": "acp",
            "transport_session_id": session_id,
            "log_path": log_path,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "exec_prepare_failed",
            "message": str(exc),
            "command": _command_to_text(safe_command),
            "cwd": safe_cwd,
            "stdout": "".join(client.assistant_chunks).strip(),
            "stderr": _tail(client.stderr_text),
            "summary": _tail(str(exc)),
            "transport": "acp",
            "transport_session_id": session_id,
            "log_path": log_path,
        }
    finally:
        with contextlib.suppress(Exception):
            await client.close()
