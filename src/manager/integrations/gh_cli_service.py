from __future__ import annotations

import asyncio
import contextlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.background_delivery import push_background_text
from core.heartbeat_store import heartbeat_store
from core.state_io import read_json, write_json
from core.state_paths import single_user_root


_DEFAULT_HOSTNAME = "github.com"
_DEFAULT_TIMEOUT_SEC = 120
_DEFAULT_AUTH_WAIT_SEC = 15
_MAX_OUTPUT_CHARS = 12000
_DEVICE_URL_PATTERN = re.compile(r"https?://[^\s]+/login/device[^\s]*", re.IGNORECASE)
_DEVICE_CODE_PATTERN = re.compile(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4})+\b")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _safe_hostname(value: Any) -> str:
    raw = str(value or _DEFAULT_HOSTNAME).strip().lower()
    if not raw:
        return _DEFAULT_HOSTNAME
    return re.sub(r"[^a-z0-9._-]+", "_", raw) or _DEFAULT_HOSTNAME


def _normalize_scopes(value: Any) -> List[str]:
    if isinstance(value, list):
        rows = [_safe_text(item, limit=120) for item in value]
        return [item for item in rows if item]
    if isinstance(value, str):
        rows = [item.strip() for item in value.split(",")]
        return [item for item in rows if item]
    return []


def _normalize_argv(value: Any) -> List[str]:
    if isinstance(value, list):
        return [
            _safe_text(item, limit=400) for item in value if _safe_text(item, limit=400)
        ]
    if isinstance(value, str):
        parts = [item.strip() for item in value.split()]
        return [item for item in parts if item]
    return []


def _truncate_output(text: str, *, limit: int = _MAX_OUTPUT_CHARS) -> str:
    payload = str(text or "")
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip() + "\n...[truncated]"


def _looks_like_auth_missing(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        token in lowered
        for token in (
            "not logged into",
            "authentication required",
            "try authenticating with",
            "gh auth login",
        )
    )


def _failure_status_from_output(text: str) -> str:
    lowered = str(text or "").lower()
    if "expired" in lowered or "device code" in lowered and "expired" in lowered:
        return "expired"
    if "cancel" in lowered:
        return "cancelled"
    return "failed"


@dataclass
class _AuthJob:
    hostname: str
    process: asyncio.subprocess.Process
    task: asyncio.Task[Any]
    ready_event: asyncio.Event


class GhCliService:
    def __init__(self) -> None:
        self._auth_jobs: Dict[str, _AuthJob] = {}
        self._auth_lock = asyncio.Lock()

    @staticmethod
    def _gh_root() -> Path:
        root = (single_user_root() / "integrations" / "gh").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def _gh_config_dir(cls) -> Path:
        target = (cls._gh_root() / "config").resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def _git_config_global(cls) -> Path:
        target = (single_user_root() / "integrations" / "git" / ".gitconfig").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def _session_path(cls, hostname: str) -> Path:
        safe_hostname = _safe_hostname(hostname)
        target = (cls._gh_root() / "sessions" / f"{safe_hostname}.md").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def _log_path(cls, hostname: str) -> Path:
        safe_hostname = _safe_hostname(hostname)
        target = (cls._gh_root() / "logs" / f"{safe_hostname}.log").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def _command_env(cls) -> Dict[str, str]:
        env = dict(os.environ)
        env["GH_CONFIG_DIR"] = str(cls._gh_config_dir())
        env["GIT_CONFIG_GLOBAL"] = str(cls._git_config_global())
        env.setdefault("PAGER", "cat")
        env.setdefault("GIT_PAGER", "cat")
        env.setdefault("GH_NO_UPDATE_NOTIFIER", "1")
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GCM_INTERACTIVE", "never")
        return env

    @staticmethod
    def _resolve_cwd(cwd: str) -> str | None:
        safe_cwd = str(cwd or "").strip()
        if not safe_cwd:
            return None
        target = Path(safe_cwd).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(f"cwd does not exist: {target}")
        return str(target)

    @staticmethod
    def _response(
        *,
        ok: bool,
        summary: str,
        text: str = "",
        data: Dict[str, Any] | None = None,
        error_code: str = "",
        terminal: bool = False,
        failure_mode: str = "",
        history_visibility: str = "normal",
        preserve_empty_text: bool = False,
        task_outcome: str = "done",
    ) -> Dict[str, Any]:
        rendered_text = text if preserve_empty_text else (text or summary)
        safe_task_outcome = _safe_text(task_outcome or "", limit=32).lower()
        payload: Dict[str, Any] = {
            "ok": bool(ok),
            "summary": _safe_text(summary, limit=500),
            "text": _safe_text(rendered_text, limit=12000),
            "data": dict(data or {}),
            "terminal": bool(terminal),
            "task_outcome": safe_task_outcome,
            "history_visibility": _safe_text(history_visibility or "normal", limit=32)
            or "normal",
        }
        if not ok:
            payload["error_code"] = _safe_text(error_code or "gh_cli_failed", limit=80)
            payload["message"] = _safe_text(text or summary, limit=12000)
            payload["failure_mode"] = (
                _safe_text(failure_mode or "fatal", limit=40) or "fatal"
            )
        return payload

    @classmethod
    async def _read_session(cls, hostname: str) -> Dict[str, Any]:
        loaded = await read_json(cls._session_path(hostname), {})
        return dict(loaded) if isinstance(loaded, dict) else {}

    @classmethod
    async def _write_session(
        cls, hostname: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        session = dict(payload or {})
        session["hostname"] = _safe_text(hostname, limit=120) or _DEFAULT_HOSTNAME
        session["updated_at"] = _now_iso()
        await write_json(cls._session_path(hostname), session)
        return session

    @classmethod
    async def _update_session(cls, hostname: str, **fields: Any) -> Dict[str, Any]:
        current = await cls._read_session(hostname)
        current.update(fields)
        return await cls._write_session(hostname, current)

    @classmethod
    def _append_log(cls, hostname: str, line: str) -> None:
        payload = str(line or "").rstrip()
        if not payload:
            return
        target = cls._log_path(hostname)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")

    async def _spawn_process(
        self,
        argv: List[str],
        *,
        cwd: str | None = None,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self._command_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _run_capture(
        self,
        argv: List[str],
        *,
        cwd: str | None = None,
        timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
    ) -> Dict[str, Any]:
        try:
            process = await self._spawn_process(argv, cwd=cwd)
        except FileNotFoundError:
            return {
                "ok": False,
                "error_code": "command_not_found",
                "summary": f"command not found: {argv[0]}",
                "text": f"command not found: {argv[0]}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error_code": "exec_prepare_failed",
                "summary": str(exc),
                "text": str(exc),
            }

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(),
                timeout=max(1, int(timeout_sec or _DEFAULT_TIMEOUT_SEC)),
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            return {
                "ok": False,
                "error_code": "timeout",
                "summary": f"command timed out after {int(timeout_sec or _DEFAULT_TIMEOUT_SEC)}s",
                "text": f"command timed out after {int(timeout_sec or _DEFAULT_TIMEOUT_SEC)}s",
            }

        stdout = (stdout_raw or b"").decode("utf-8", errors="replace")
        stderr = (stderr_raw or b"").decode("utf-8", errors="replace")
        combined = stdout.strip()
        if stderr.strip():
            combined = (
                f"{combined}\n[stderr]\n{stderr.strip()}"
                if combined
                else f"[stderr]\n{stderr.strip()}"
            )
        return {
            "ok": process.returncode == 0,
            "exit_code": int(process.returncode or 0),
            "stdout": stdout,
            "stderr": stderr,
            "output": _truncate_output(combined),
        }

    async def _resolve_notify(self, notify: Dict[str, Any]) -> Dict[str, str]:
        payload = {
            "platform": _safe_text(notify.get("platform"), limit=64),
            "chat_id": _safe_text(notify.get("chat_id"), limit=128),
            "user_id": _safe_text(notify.get("user_id"), limit=80),
            "session_id": _safe_text(notify.get("session_id"), limit=120),
        }
        if payload["platform"] and payload["chat_id"]:
            return payload
        if not payload["user_id"]:
            return payload
        target = await heartbeat_store.get_delivery_target(payload["user_id"])
        if not payload["platform"]:
            payload["platform"] = _safe_text(target.get("platform"), limit=64)
        if not payload["chat_id"]:
            payload["chat_id"] = _safe_text(target.get("chat_id"), limit=128)
        if not payload["session_id"]:
            payload["session_id"] = _safe_text(target.get("session_id"), limit=120)
        return payload

    async def _push_notify(self, notify: Dict[str, Any], text: str) -> None:
        payload = await self._resolve_notify(notify)
        if not payload.get("platform") or not payload.get("chat_id"):
            return
        await push_background_text(
            platform=payload["platform"],
            chat_id=payload["chat_id"],
            text=text,
            filename_prefix="gh-auth",
            record_history=bool(payload.get("user_id")),
            history_user_id=payload.get("user_id", ""),
            history_session_id=payload.get("session_id", ""),
        )

    async def _auth_status_command(self, hostname: str) -> Dict[str, Any]:
        result = await self._run_capture(
            ["gh", "auth", "status", "--hostname", hostname],
            timeout_sec=30,
        )
        text = _safe_text(result.get("output"), limit=12000)
        return {
            "authenticated": bool(result.get("ok")),
            "text": text,
            "raw": result,
        }

    def _compose_waiting_text(self, session: Dict[str, Any]) -> str:
        lines = ["已发起 GitHub 设备码登录，我会在后台继续等待授权完成。", ""]
        verification_uri = _safe_text(session.get("verification_uri"), limit=500)
        user_code = _safe_text(session.get("user_code"), limit=120)
        if verification_uri:
            lines.append(f"- 授权地址: {verification_uri}")
        if user_code:
            lines.append(f"- 设备码: `{user_code}`")
        lines.extend(
            [
                f"- 凭据目录: `{self._gh_config_dir()}`",
                f"- Git 配置: `{self._git_config_global()}`",
                "",
                "如果想查询最新状态，可再执行 `gh_cli` 的 `auth_status`。",
            ]
        )
        return "\n".join(lines).strip()

    @staticmethod
    def _compose_auth_probe_text(
        *,
        hostname: str,
        authenticated: bool,
        status_text: str,
    ) -> str:
        if authenticated:
            return ""
        if status_text:
            return (
                f"GitHub CLI 当前未登录（{hostname}）。"
                "只有在后续步骤确实需要认证 GitHub 操作时，才需要再发起 `auth_start`。"
            )
        return f"GitHub CLI 认证状态未知（{hostname}），可按需继续后续任务。"

    def _compose_success_text(
        self,
        hostname: str,
        *,
        status_text: str,
        setup_git_ok: bool,
        setup_git_text: str,
    ) -> str:
        lines = [f"GitHub 登录已完成（{hostname}）。"]
        if status_text:
            lines.extend(["", status_text])
        lines.extend(
            [
                "",
                f"- 凭据目录: `{self._gh_config_dir()}`",
                f"- Git 配置: `{self._git_config_global()}`",
            ]
        )
        if setup_git_ok:
            lines.append("- `gh auth setup-git` 已完成")
        elif setup_git_text:
            lines.append(f"- `gh auth setup-git` 未完成: {setup_git_text[:240]}")
        return "\n".join(lines).strip()

    def _compose_failure_text(self, session: Dict[str, Any]) -> str:
        status = _safe_text(session.get("status"), limit=40) or "failed"
        detail = (
            _safe_text(session.get("last_error"), limit=1200)
            or _safe_text(session.get("last_output"), limit=1200)
            or "设备码登录未完成。"
        )
        return (
            f"GitHub 设备码登录未完成（状态: `{status}`）。\n\n"
            f"{detail}\n\n"
            "请重新执行 `gh_cli` 的 `auth_start` 发起新的设备码登录。"
        )

    async def _read_stream(
        self,
        *,
        hostname: str,
        reader: asyncio.StreamReader | None,
        stream_name: str,
        ready_event: asyncio.Event,
    ) -> None:
        if reader is None:
            return
        while True:
            raw = await reader.readline()
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip()
            self._append_log(hostname, f"[{stream_name}] {text}")
            if not text:
                continue
            session = await self._read_session(hostname)
            verification_uri = _safe_text(session.get("verification_uri"), limit=500)
            user_code = _safe_text(session.get("user_code"), limit=120)
            url_match = _DEVICE_URL_PATTERN.search(text)
            code_match = _DEVICE_CODE_PATTERN.search(text)
            updated = False
            if url_match and not verification_uri:
                verification_uri = _safe_text(url_match.group(0), limit=500)
                updated = True
            if code_match and not user_code:
                user_code = _safe_text(code_match.group(0), limit=120)
                updated = True
            last_output_key = "last_output" if stream_name == "stdout" else "last_error"
            if updated or not _safe_text(session.get(last_output_key), limit=1200):
                session[last_output_key] = _safe_text(text, limit=1200)
            if verification_uri:
                session["verification_uri"] = verification_uri
            if user_code:
                session["user_code"] = user_code
            if verification_uri and user_code and session.get("status") == "starting":
                session["status"] = "waiting_user"
                session.setdefault("waiting_started_at", _now_iso())
                updated = True
            if updated:
                await self._write_session(hostname, session)
            if verification_uri and user_code and not ready_event.is_set():
                ready_event.set()

    def _job_for(self, hostname: str) -> _AuthJob | None:
        safe_hostname = _safe_hostname(hostname)
        job = self._auth_jobs.get(safe_hostname)
        if job is not None and job.task.done():
            self._auth_jobs.pop(safe_hostname, None)
            return None
        return job

    async def _monitor_auth_job(
        self,
        *,
        hostname: str,
        process: asyncio.subprocess.Process,
        ready_event: asyncio.Event,
    ) -> None:
        await asyncio.gather(
            self._read_stream(
                hostname=hostname,
                reader=process.stdout,
                stream_name="stdout",
                ready_event=ready_event,
            ),
            self._read_stream(
                hostname=hostname,
                reader=process.stderr,
                stream_name="stderr",
                ready_event=ready_event,
            ),
        )
        return_code = await process.wait()
        session = await self._read_session(hostname)
        session["exit_code"] = int(return_code or 0)
        session["completed_at"] = _now_iso()

        if int(return_code or 0) == 0:
            status_result = await self._auth_status_command(hostname)
            setup_git = await self._run_capture(
                ["gh", "auth", "setup-git"],
                timeout_sec=30,
            )
            success_text = self._compose_success_text(
                hostname,
                status_text=_safe_text(status_result.get("text"), limit=4000),
                setup_git_ok=bool(setup_git.get("ok")),
                setup_git_text=_safe_text(setup_git.get("output"), limit=600),
            )
            session.update(
                {
                    "status": "authenticated",
                    "last_output": _safe_text(status_result.get("text"), limit=1200),
                    "setup_git": {
                        "ok": bool(setup_git.get("ok")),
                        "summary": _safe_text(
                            setup_git.get("output") or "gh auth setup-git finished",
                            limit=600,
                        ),
                    },
                    "notify_sent_at": _now_iso(),
                }
            )
            await self._write_session(hostname, session)
            await self._push_notify(dict(session.get("notify") or {}), success_text)
        else:
            combined = "\n".join(
                [
                    _safe_text(session.get("last_output"), limit=1200),
                    _safe_text(session.get("last_error"), limit=1200),
                ]
            ).strip()
            session["status"] = _failure_status_from_output(combined)
            session["notify_sent_at"] = _now_iso()
            await self._write_session(hostname, session)
            await self._push_notify(
                dict(session.get("notify") or {}), self._compose_failure_text(session)
            )

        if not ready_event.is_set():
            ready_event.set()
        self._auth_jobs.pop(_safe_hostname(hostname), None)

    @staticmethod
    def _process_alive(pid: Any) -> bool:
        try:
            safe_pid = int(pid or 0)
        except Exception:
            return False
        if safe_pid <= 0:
            return False
        try:
            os.kill(safe_pid, 0)
        except OSError:
            return False
        return True

    async def auth_start(
        self,
        *,
        hostname: str = _DEFAULT_HOSTNAME,
        scopes: Any = None,
        notify_platform: str = "",
        notify_chat_id: str = "",
        notify_user_id: str = "",
    ) -> Dict[str, Any]:
        safe_hostname = _safe_text(hostname, limit=120) or _DEFAULT_HOSTNAME
        auth_status = await self._auth_status_command(safe_hostname)
        if bool(auth_status.get("authenticated")):
            return self._response(
                ok=True,
                summary=f"already authenticated for {safe_hostname}",
                text=self._compose_success_text(
                    safe_hostname,
                    status_text=_safe_text(auth_status.get("text"), limit=4000),
                    setup_git_ok=False,
                    setup_git_text="",
                ),
                data={"status": "authenticated", "hostname": safe_hostname},
            )

        async with self._auth_lock:
            job = self._job_for(safe_hostname)
            if job is not None and self._process_alive(getattr(job.process, "pid", 0)):
                session = await self._read_session(safe_hostname)
                return self._response(
                    ok=True,
                    summary=f"auth already waiting for {safe_hostname}",
                    text=self._compose_waiting_text(session),
                    data={"session": session},
                )

            scope_list = _normalize_scopes(scopes)
            argv = [
                "gh",
                "auth",
                "login",
                "--web",
                "--hostname",
                safe_hostname,
                "--git-protocol",
                "https",
            ]
            if scope_list:
                argv.extend(["--scopes", ",".join(scope_list)])

            session = await self._write_session(
                safe_hostname,
                {
                    "status": "starting",
                    "hostname": safe_hostname,
                    "verification_uri": "",
                    "user_code": "",
                    "started_at": _now_iso(),
                    "completed_at": "",
                    "exit_code": "",
                    "notify": {
                        "platform": _safe_text(notify_platform, limit=64),
                        "chat_id": _safe_text(notify_chat_id, limit=128),
                        "user_id": _safe_text(notify_user_id, limit=80),
                    },
                    "gh_config_dir": str(self._gh_config_dir()),
                    "git_config_global": str(self._git_config_global()),
                    "last_output": "",
                    "last_error": "",
                    "scopes": scope_list,
                },
            )
            self._append_log(safe_hostname, f"[{_now_iso()}] start: {' '.join(argv)}")
            try:
                process = await self._spawn_process(argv)
            except FileNotFoundError:
                await self._update_session(
                    safe_hostname,
                    status="failed",
                    last_error="command not found: gh",
                    completed_at=_now_iso(),
                )
                return self._response(
                    ok=False,
                    summary="gh command not found",
                    text="未找到 `gh` 命令，请先在运行环境内安装 GitHub CLI。",
                    error_code="command_not_found",
                )
            except Exception as exc:
                await self._update_session(
                    safe_hostname,
                    status="failed",
                    last_error=str(exc),
                    completed_at=_now_iso(),
                )
                return self._response(
                    ok=False,
                    summary="failed to start gh auth login",
                    text=str(exc),
                    error_code="auth_start_failed",
                )

            ready_event = asyncio.Event()
            await self._update_session(safe_hostname, pid=int(process.pid or 0))
            task = asyncio.create_task(
                self._monitor_auth_job(
                    hostname=safe_hostname,
                    process=process,
                    ready_event=ready_event,
                ),
                name=f"gh-auth:{_safe_hostname(safe_hostname)}",
            )
            self._auth_jobs[_safe_hostname(safe_hostname)] = _AuthJob(
                hostname=safe_hostname,
                process=process,
                task=task,
                ready_event=ready_event,
            )

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ready_event.wait(), timeout=_DEFAULT_AUTH_WAIT_SEC)

        session = await self._read_session(safe_hostname)
        status = _safe_text(session.get("status"), limit=40).lower()
        if status == "authenticated":
            return self._response(
                ok=True,
                summary=f"authenticated for {safe_hostname}",
                text=self._compose_success_text(
                    safe_hostname,
                    status_text=_safe_text(session.get("last_output"), limit=4000),
                    setup_git_ok=bool(dict(session.get("setup_git") or {}).get("ok")),
                    setup_git_text=_safe_text(
                        dict(session.get("setup_git") or {}).get("summary"),
                        limit=600,
                    ),
                ),
                data={"session": session},
            )
        if status in {"failed", "expired", "cancelled"}:
            return self._response(
                ok=False,
                summary=f"auth failed for {safe_hostname}",
                text=self._compose_failure_text(session),
                error_code=f"auth_{status}",
            )
        return self._response(
            ok=True,
            summary=f"auth waiting for {safe_hostname}",
            text=self._compose_waiting_text(session),
            data={"session": session},
        )

    async def auth_status(self, *, hostname: str = _DEFAULT_HOSTNAME) -> Dict[str, Any]:
        safe_hostname = _safe_text(hostname, limit=120) or _DEFAULT_HOSTNAME
        session = await self._read_session(safe_hostname)
        session_status = _safe_text(session.get("status"), limit=40).lower()
        job = self._job_for(safe_hostname)

        if session_status == "waiting_user":
            if job is not None and self._process_alive(getattr(job.process, "pid", 0)):
                return self._response(
                    ok=True,
                    summary=f"auth still waiting for {safe_hostname}",
                    text=self._compose_waiting_text(session),
                    data={"session": session},
                    task_outcome="",
                )
            session = await self._update_session(
                safe_hostname,
                status="interrupted",
                completed_at=_now_iso(),
            )
            return self._response(
                ok=True,
                summary=f"auth interrupted for {safe_hostname}",
                text=self._compose_failure_text(session),
                data={"session": session},
                task_outcome="",
            )

        status_result = await self._auth_status_command(safe_hostname)
        if bool(status_result.get("authenticated")):
            merged_session = await self._update_session(
                safe_hostname,
                status="authenticated",
                completed_at=_safe_text(session.get("completed_at"), limit=80)
                or _now_iso(),
                last_output=_safe_text(status_result.get("text"), limit=1200),
            )
            return self._response(
                ok=True,
                summary=f"auth probe ok for {safe_hostname}",
                text=self._compose_auth_probe_text(
                    hostname=safe_hostname,
                    authenticated=True,
                    status_text=_safe_text(status_result.get("text"), limit=4000),
                ),
                data={"session": merged_session, "auth_status": status_result},
                history_visibility="suppress_success",
                preserve_empty_text=True,
                task_outcome="",
            )

        if session_status in {"failed", "expired", "cancelled", "interrupted"}:
            return self._response(
                ok=True,
                summary=f"auth probe found inactive session for {safe_hostname}",
                text=self._compose_auth_probe_text(
                    hostname=safe_hostname,
                    authenticated=False,
                    status_text=_safe_text(status_result.get("text"), limit=2000),
                ),
                data={"session": session, "auth_status": status_result},
                task_outcome="",
            )

        return self._response(
            ok=True,
            summary=f"auth probe found no login for {safe_hostname}",
            text=self._compose_auth_probe_text(
                hostname=safe_hostname,
                authenticated=False,
                status_text=_safe_text(status_result.get("text"), limit=2000),
            ),
            data={"session": session, "auth_status": status_result},
            task_outcome="",
        )

    async def auth_cancel(self, *, hostname: str = _DEFAULT_HOSTNAME) -> Dict[str, Any]:
        safe_hostname = _safe_text(hostname, limit=120) or _DEFAULT_HOSTNAME
        job = self._job_for(safe_hostname)
        if job is None:
            session = await self._read_session(safe_hostname)
            if _safe_text(session.get("status"), limit=40).lower() == "waiting_user":
                session = await self._update_session(
                    safe_hostname,
                    status="cancelled",
                    completed_at=_now_iso(),
                )
            return self._response(
                ok=True,
                summary=f"no active auth session for {safe_hostname}",
                text="当前没有等待中的 GitHub 登录会话。",
                data={"session": session},
            )

        with contextlib.suppress(ProcessLookupError):
            job.process.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(job.process.wait(), timeout=5)
        if not job.task.done():
            job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job.task

        session = await self._update_session(
            safe_hostname,
            status="cancelled",
            completed_at=_now_iso(),
            last_error="cancelled by user",
        )
        self._auth_jobs.pop(_safe_hostname(safe_hostname), None)
        return self._response(
            ok=True,
            summary=f"auth cancelled for {safe_hostname}",
            text="已取消当前 GitHub 设备码登录。",
            data={"session": session},
        )

    async def exec(
        self,
        *,
        argv: Any,
        cwd: str = "",
        timeout_sec: Any = _DEFAULT_TIMEOUT_SEC,
    ) -> Dict[str, Any]:
        normalized = _normalize_argv(argv)
        if not normalized:
            return self._response(
                ok=False,
                summary="gh argv is required",
                text="`argv` 不能为空。",
                error_code="invalid_args",
                task_outcome="",
            )

        lowered = [item.lower() for item in normalized]
        if lowered[:2] == ["auth", "login"]:
            return self._response(
                ok=False,
                summary="interactive auth login is blocked",
                text="交互式登录请使用 `action=auth_start`，不要直接用 `exec` 调 `gh auth login`。",
                error_code="interactive_command_blocked",
                task_outcome="",
            )
        if tuple(lowered[:2]) in {
            ("auth", "token"),
            ("auth", "logout"),
            ("auth", "refresh"),
        }:
            return self._response(
                ok=False,
                summary="sensitive auth command is blocked",
                text="这个 `gh auth` 子命令已被禁用，请改用专门动作或容器内人工处理。",
                error_code="sensitive_command_blocked",
                task_outcome="",
            )

        try:
            resolved_cwd = self._resolve_cwd(cwd)
        except FileNotFoundError as exc:
            return self._response(
                ok=False,
                summary="invalid cwd",
                text=str(exc),
                error_code="invalid_cwd",
                task_outcome="",
            )

        result = await self._run_capture(
            ["gh", *normalized],
            cwd=resolved_cwd,
            timeout_sec=max(1, int(timeout_sec or _DEFAULT_TIMEOUT_SEC)),
        )
        output = _safe_text(result.get("output"), limit=12000)
        if bool(result.get("ok")):
            return self._response(
                ok=True,
                summary=f"gh {' '.join(normalized)} completed",
                text=output or f"gh {' '.join(normalized)} completed.",
                data={
                    "argv": normalized,
                    "cwd": resolved_cwd or "",
                    "exit_code": int(result.get("exit_code") or 0),
                    "stdout": str(result.get("stdout") or ""),
                    "stderr": str(result.get("stderr") or ""),
                },
                task_outcome="",
            )

        error_code = "gh_command_failed"
        if _looks_like_auth_missing(output):
            error_code = "not_authenticated"
        return self._response(
            ok=False,
            summary=f"gh {' '.join(normalized)} failed",
            text=output or "gh command failed",
            data={
                "argv": normalized,
                "cwd": resolved_cwd or "",
                "exit_code": int(result.get("exit_code") or 1),
                "stdout": str(result.get("stdout") or ""),
                "stderr": str(result.get("stderr") or ""),
            },
            error_code=error_code,
            task_outcome="",
        )

    async def handle(
        self,
        *,
        action: str,
        hostname: str = _DEFAULT_HOSTNAME,
        scopes: Any = None,
        argv: Any = None,
        cwd: str = "",
        timeout_sec: Any = _DEFAULT_TIMEOUT_SEC,
        notify_platform: str = "",
        notify_chat_id: str = "",
        notify_user_id: str = "",
    ) -> Dict[str, Any]:
        safe_action = _safe_text(action, limit=80).lower() or "auth_status"
        if safe_action == "auth_start":
            return await self.auth_start(
                hostname=hostname,
                scopes=scopes,
                notify_platform=notify_platform,
                notify_chat_id=notify_chat_id,
                notify_user_id=notify_user_id,
            )
        if safe_action == "auth_status":
            return await self.auth_status(hostname=hostname)
        if safe_action == "auth_cancel":
            return await self.auth_cancel(hostname=hostname)
        if safe_action == "exec":
            return await self.exec(argv=argv, cwd=cwd, timeout_sec=timeout_sec)
        return self._response(
            ok=False,
            summary="unsupported gh_cli action",
            text=f"Unsupported gh_cli action: {safe_action}",
            error_code="unsupported_action",
            task_outcome="",
        )


gh_cli_service = GhCliService()
