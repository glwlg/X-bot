import asyncio
import contextlib
import inspect
import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from core.worker_store import worker_registry, worker_task_store
from core.tool_access_store import tool_access_store


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


SHELL_COMMAND_HINTS = {
    "echo",
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "git",
    "docker",
    "uv",
    "python",
    "python3",
    "pip",
    "npm",
    "pnpm",
    "yarn",
    "bash",
    "sh",
    "zsh",
    "curl",
    "wget",
    "make",
    "pytest",
}


class _WorkerSilentAdapter:
    async def reply_text(self, ctx, text: str, ui=None, **kwargs):
        return SimpleNamespace(id=f"worker-silent-{int(datetime.now().timestamp())}")

    async def edit_text(self, ctx, message_id: str, text: str, **kwargs):
        return SimpleNamespace(id=message_id)

    async def reply_document(self, ctx, document, filename=None, caption=None, **kwargs):
        return SimpleNamespace(id=filename or "doc")

    async def reply_photo(self, ctx, photo, caption=None, **kwargs):
        return SimpleNamespace(id="photo")

    async def reply_video(self, ctx, video, caption=None, **kwargs):
        return SimpleNamespace(id="video")

    async def reply_audio(self, ctx, audio, caption=None, **kwargs):
        return SimpleNamespace(id="audio")

    async def delete_message(self, ctx, message_id: str, chat_id=None, **kwargs):
        return True

    async def send_chat_action(self, ctx, action: str, chat_id=None, **kwargs):
        return True

    async def download_file(self, ctx, file_id: str, **kwargs) -> bytes:
        raise RuntimeError("worker runtime context does not support file download")


class WorkerRuntime:
    """Execute worker tasks in local mode or isolated docker worker mode."""

    def __init__(self):
        self.exec_timeout_sec = max(30, int(os.getenv("WORKER_EXEC_TIMEOUT_SEC", "900")))
        self.auth_status_timeout_sec = max(
            5, int(os.getenv("WORKER_AUTH_STATUS_TIMEOUT_SEC", "45"))
        )
        self.codex_cmd = os.getenv("WORKER_CODEX_COMMAND", "codex").strip() or "codex"
        self.gemini_cmd = (
            os.getenv("WORKER_GEMINI_CLI_COMMAND", "gemini-cli").strip() or "gemini-cli"
        )
        self.codex_args_template = (
            os.getenv("WORKER_CODEX_ARGS_TEMPLATE", "exec {instruction}").strip()
            or "exec {instruction}"
        )
        self.gemini_args_template = (
            os.getenv("WORKER_GEMINI_ARGS_TEMPLATE", "--prompt {instruction}").strip()
            or "--prompt {instruction}"
        )
        self.shell_cmd = os.getenv("WORKER_SHELL_COMMAND", "sh").strip() or "sh"
        self.runtime_mode = (
            os.getenv("WORKER_RUNTIME_MODE", "local").strip().lower() or "local"
        )
        if self.runtime_mode not in {"local", "docker"}:
            self.runtime_mode = "local"
        self.docker_container = (
            os.getenv("WORKER_DOCKER_CONTAINER", "x-bot-worker").strip()
            or "x-bot-worker"
        )
        self.codex_auth_start_args = (
            os.getenv("WORKER_CODEX_AUTH_START_ARGS", "auth login").strip() or "auth login"
        )
        self.gemini_auth_start_args = (
            os.getenv("WORKER_GEMINI_AUTH_START_ARGS", "auth login").strip() or "auth login"
        )
        self.codex_auth_status_args = (
            os.getenv("WORKER_CODEX_AUTH_STATUS_ARGS", "auth status").strip()
            or "auth status"
        )
        self.gemini_auth_status_args = (
            os.getenv("WORKER_GEMINI_AUTH_STATUS_ARGS", "auth status").strip()
            or "auth status"
        )
        self.fallback_to_core_agent = (
            os.getenv("WORKER_FALLBACK_CORE_AGENT", "true").strip().lower() == "true"
        )
        self.data_dir = os.getenv("DATA_DIR", "/app/data").strip() or "/app/data"
        self.userland_root = (
            os.getenv(
                "USERLAND_ROOT",
                os.path.join(self.data_dir, "userland", "workers"),
            ).strip()
            or os.path.join(self.data_dir, "userland", "workers")
        )
        self.docker_data_dir = (
            os.getenv("WORKER_DOCKER_DATA_DIR", "/app/data").strip() or "/app/data"
        )

    @staticmethod
    def _normalize_backend(value: str | None) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"core", "core-agent", "manager", "orchestrator", "builtin"}:
            return "core-agent"
        if raw in {"shell", "bash", "sh"}:
            return "shell"
        if raw in {"codex", "openai-codex"}:
            return "codex"
        if raw in {"gemini", "gemini_cli", "gemini-cli"}:
            return "gemini-cli"
        return "core-agent"

    @staticmethod
    def _normalize_provider(value: str | None) -> str:
        return WorkerRuntime._normalize_backend(value)

    @staticmethod
    def _looks_like_shell_command(text: str) -> bool:
        raw = str(text or "").strip()
        if not raw or "\n" in raw:
            return False
        try:
            parts = shlex.split(raw)
        except Exception:
            return False
        if not parts:
            return False
        first = parts[0]
        if first in SHELL_COMMAND_HINTS:
            return True
        if first.startswith("./") or first.startswith("../") or first.startswith("/"):
            return True
        return False

    def _build_command(self, backend: str, instruction: str) -> tuple[str, list[str]]:
        safe_instruction = str(instruction or "").strip()
        if backend == "shell":
            return self.shell_cmd, ["-lc", safe_instruction]
        if backend == "gemini-cli":
            args = self.gemini_args_template.format(
                instruction=shlex.quote(safe_instruction)
            )
            return self.gemini_cmd, shlex.split(args)
        args = self.codex_args_template.format(instruction=shlex.quote(safe_instruction))
        return self.codex_cmd, shlex.split(args)

    def _resolve_runtime_workspace(self, worker: Dict[str, Any]) -> Path:
        worker_id = str(worker.get("id") or "worker-main").strip() or "worker-main"
        if self.runtime_mode == "docker":
            return (
                Path(self.docker_data_dir) / "userland" / "workers" / worker_id
            ).resolve()
        configured = str(worker.get("workspace_root") or "").strip()
        if configured:
            return Path(configured).resolve()
        return (Path(self.userland_root) / worker_id).resolve()

    async def _spawn_local_process(
        self,
        cmd: str,
        args: list[str],
        workspace: Path,
    ) -> Dict[str, Any]:
        cmd_path = shutil.which(cmd)
        if not cmd_path:
            return {
                "ok": False,
                "error": (
                    f"CLI not found: {cmd}. "
                    "Please authorize/install this backend first."
                ),
            }
        try:
            process = await asyncio.create_subprocess_exec(
                cmd_path,
                *args,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return {"ok": True, "process": process}
        except Exception as exc:
            return {"ok": False, "error": f"failed to spawn local process: {exc}"}

    async def _is_worker_container_running(self) -> Dict[str, Any]:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return {"ok": False, "error": "docker CLI not found in kernel container."}
        try:
            process = await asyncio.create_subprocess_exec(
                docker_bin,
                "ps",
                "--format",
                "{{.Names}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        except Exception as exc:
            return {"ok": False, "error": f"failed to inspect docker containers: {exc}"}

        if process.returncode != 0:
            err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
            return {
                "ok": False,
                "error": (
                    "docker ps failed while checking worker container: "
                    f"{err_text or process.returncode}"
                ),
            }

        names = {
            line.strip()
            for line in (stdout or b"").decode("utf-8", errors="replace").splitlines()
            if line.strip()
        }
        if self.docker_container not in names:
            return {
                "ok": False,
                "error": (
                    f"worker container `{self.docker_container}` is not running. "
                    "Run `docker compose up -d x-bot-worker` first."
                ),
            }
        return {"ok": True}

    async def _container_has_command(self, cmd: str) -> Dict[str, Any]:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return {"ok": False, "error": "docker CLI not found in kernel container."}
        check_cmd = f"command -v {shlex.quote(cmd)} >/dev/null 2>&1"
        try:
            process = await asyncio.create_subprocess_exec(
                docker_bin,
                "exec",
                self.docker_container,
                "sh",
                "-lc",
                check_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await process.communicate()
        except Exception as exc:
            return {"ok": False, "error": f"failed to probe CLI in worker container: {exc}"}
        if process.returncode != 0:
            err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
            return {
                "ok": False,
                "error": (
                    f"CLI `{cmd}` is unavailable in `{self.docker_container}`."
                    + (f" stderr: {err_text}" if err_text else "")
                ),
            }
        return {"ok": True}

    async def _spawn_docker_process(
        self,
        cmd: str,
        args: list[str],
        workspace: Path,
    ) -> Dict[str, Any]:
        running = await self._is_worker_container_running()
        if not running.get("ok"):
            return running

        has_cli = await self._container_has_command(cmd)
        if not has_cli.get("ok"):
            return has_cli

        docker_bin = shutil.which("docker")
        if not docker_bin:
            return {"ok": False, "error": "docker CLI not found in kernel container."}

        try:
            process = await asyncio.create_subprocess_exec(
                docker_bin,
                "exec",
                "-i",
                "-w",
                str(workspace),
                self.docker_container,
                cmd,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return {"ok": True, "process": process}
        except Exception as exc:
            return {"ok": False, "error": f"failed to spawn docker worker process: {exc}"}

    async def _spawn_worker_process(
        self,
        cmd: str,
        args: list[str],
        workspace: Path,
    ) -> Dict[str, Any]:
        if self.runtime_mode == "docker":
            return await self._spawn_docker_process(cmd=cmd, args=args, workspace=workspace)
        return await self._spawn_local_process(cmd=cmd, args=args, workspace=workspace)

    @staticmethod
    def _combine_output(stdout: bytes | None, stderr: bytes | None) -> str:
        out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        if err_text:
            return f"{out_text}\n[stderr]\n{err_text}".strip() if out_text else f"[stderr]\n{err_text}"
        return out_text

    def _should_retry_codex_without_instruction_flag(
        self,
        *,
        backend: str,
        output: str,
    ) -> bool:
        if backend != "codex":
            return False
        if "--instruction" not in self.codex_args_template:
            return False
        lowered = str(output or "").lower()
        return "unexpected argument '--instruction'" in lowered

    async def _execute_command(
        self,
        *,
        cmd: str,
        args: list[str],
        workspace: Path,
        timeout_sec: int,
    ) -> Dict[str, Any]:
        spawn = await self._spawn_worker_process(cmd=cmd, args=args, workspace=workspace)
        if not spawn.get("ok"):
            return {
                "ok": False,
                "error": "prepare_failed",
                "message": str(spawn.get("error") or "failed to prepare worker process"),
                "exit_code": -1,
                "stdout": b"",
                "stderr": b"",
            }

        process = spawn["process"]
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            return {
                "ok": False,
                "error": "timeout",
                "message": f"Worker task timeout after {timeout_sec}s",
                "exit_code": -1,
                "stdout": b"",
                "stderr": b"",
            }
        return {
            "ok": process.returncode == 0,
            "error": "",
            "message": "",
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    @staticmethod
    def _coerce_async_stream(stream: Any):
        if hasattr(stream, "__aiter__"):
            return stream
        if inspect.isawaitable(stream):
            async def _single():
                result = await stream
                if result is not None:
                    yield str(result)

            return _single()
        raise TypeError("agent_orchestrator.handle_message must return an async iterator")

    async def _execute_core_agent_task(
        self,
        *,
        worker_id: str,
        instruction: str,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        try:
            from core.agent_orchestrator import agent_orchestrator
            from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
        except Exception as exc:
            return {
                "ok": False,
                "error": f"core_agent_import_error:{exc}",
                "summary": f"core-agent import failed: {exc}",
                "result": "",
            }

        meta = metadata or {}
        logical_user = (
            str(meta.get("user_id") or meta.get("chat_id") or worker_id).strip()
            or worker_id
        )
        worker_user_id = f"worker::{worker_id}::{logical_user}"
        now = datetime.now()
        user = User(
            id=worker_user_id,
            username=f"worker_{worker_id}",
            first_name="Worker",
            last_name="Agent",
        )
        chat = Chat(
            id=worker_user_id,
            type="private",
            title=f"worker-{worker_id}",
        )
        message = UnifiedMessage(
            id=f"worker-msg-{int(now.timestamp())}",
            platform="worker_runtime",
            user=user,
            chat=chat,
            date=now,
            type=MessageType.TEXT,
            text=str(instruction or ""),
        )
        ctx = UnifiedContext(
            message=message,
            platform_ctx=None,
            platform_event=None,
            _adapter=_WorkerSilentAdapter(),
            user=user,
        )
        ctx.user_data["execution_policy"] = "worker_execution_policy"
        message_history = [{"role": "user", "parts": [{"text": str(instruction or "")}]}]

        try:
            handler = getattr(agent_orchestrator, "handle_message", None)
            if handler is None:
                raise RuntimeError("agent_orchestrator.handle_message is unavailable")
            raw_stream = handler(ctx, message_history)
            stream = self._coerce_async_stream(raw_stream)
            chunks: list[str] = []
            async for chunk in stream:
                if chunk:
                    chunks.append(str(chunk))
            final_text = "\n".join(chunks).strip()
            if not final_text:
                final_text = "Worker core-agent finished with no text output."
            return {
                "ok": True,
                "error": "",
                "summary": final_text[:500],
                "result": final_text,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"core_agent_exec_error:{exc}",
                "summary": f"core-agent execution failed: {exc}",
                "result": "",
            }

    def _auth_command(self, provider: str, action: str) -> tuple[str, list[str]]:
        normalized = self._normalize_provider(provider)
        if normalized == "gemini-cli":
            cmd = self.gemini_cmd
            raw_args = (
                self.gemini_auth_start_args
                if action == "start"
                else self.gemini_auth_status_args
            )
        else:
            cmd = self.codex_cmd
            raw_args = self.codex_auth_start_args if action == "start" else self.codex_auth_status_args
        args = shlex.split(raw_args) if raw_args.strip() else []
        return cmd, args

    async def build_auth_start_command(self, worker_id: str, provider: str) -> Dict[str, Any]:
        worker = await worker_registry.get_worker(worker_id)
        if not worker:
            return {
                "ok": False,
                "error": f"worker_not_found:{worker_id}",
                "summary": "Worker not found.",
            }
        workspace = self._resolve_runtime_workspace(worker)
        workspace.mkdir(parents=True, exist_ok=True)
        cmd, args = self._auth_command(provider, action="start")
        backend_cmd = shlex.join([cmd, *args]) if args else cmd
        if self.runtime_mode == "docker":
            inner = f"cd {shlex.quote(str(workspace))} && {backend_cmd}"
            manual = (
                f"docker exec -it {shlex.quote(self.docker_container)} "
                f"sh -lc {shlex.quote(inner)}"
            )
        else:
            manual = f"cd {shlex.quote(str(workspace))} && {backend_cmd}"
        return {
            "ok": True,
            "provider": self._normalize_provider(provider),
            "worker_id": str(worker.get("id") or worker_id),
            "workspace_root": str(workspace),
            "runtime_mode": self.runtime_mode,
            "command": manual,
            "summary": "Run this command manually to complete interactive auth login.",
        }

    async def check_auth_status(self, worker_id: str, provider: str) -> Dict[str, Any]:
        worker = await worker_registry.get_worker(worker_id)
        if not worker:
            return {
                "ok": False,
                "error": f"worker_not_found:{worker_id}",
                "status": "unknown",
                "authenticated": False,
                "summary": "Worker not found.",
            }
        workspace = self._resolve_runtime_workspace(worker)
        workspace.mkdir(parents=True, exist_ok=True)
        cmd, args = self._auth_command(provider, action="status")
        if not args:
            return {
                "ok": False,
                "error": "auth_status_command_missing",
                "status": "unknown",
                "authenticated": False,
                "summary": "Auth status command is not configured.",
            }

        run = await self._execute_command(
            cmd=cmd,
            args=args,
            workspace=workspace,
            timeout_sec=self.auth_status_timeout_sec,
        )
        text = self._combine_output(run.get("stdout"), run.get("stderr"))
        lowered = text.lower()

        not_authed_tokens = (
            "not logged",
            "unauth",
            "not authorized",
            "login required",
            "未登录",
            "未认证",
        )
        authed_tokens = (
            "logged in",
            "authenticated",
            "authorized",
            "已登录",
            "已认证",
        )

        is_not_authed = any(token in lowered for token in not_authed_tokens)
        is_authed = (not is_not_authed) and any(token in lowered for token in authed_tokens)
        if is_authed:
            status = "authenticated"
        elif is_not_authed:
            status = "not_authenticated"
        elif run.get("ok"):
            status = "ok_unknown"
        else:
            status = "unknown"

        summary = text[:500] or str(run.get("message") or "")
        return {
            "ok": bool(run.get("ok")),
            "status": status,
            "authenticated": status == "authenticated",
            "runtime_mode": self.runtime_mode,
            "provider": self._normalize_provider(provider),
            "exit_code": int(run.get("exit_code", -1)),
            "summary": summary,
            "error": str(run.get("error") or ""),
        }

    async def execute_task(
        self,
        *,
        worker_id: str,
        source: str,
        instruction: str,
        backend: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        worker = await worker_registry.get_worker(worker_id)
        if not worker:
            return {
                "ok": False,
                "error": f"worker_not_found:{worker_id}",
                "summary": "Worker not found.",
            }

        task = await worker_task_store.create_task(
            worker_id=worker["id"],
            source=source,
            instruction=instruction,
            metadata=metadata or {},
        )
        task_id = str(task["task_id"])
        await worker_task_store.update_task(
            task_id,
            status="running",
            started_at=task["created_at"],
            retry_count=0,
        )
        await worker_registry.update_worker(
            worker["id"],
            status="busy",
            last_task_id=task_id,
            last_error="",
        )

        normalized_source = str(source or "").strip().lower()
        selected_backend = self._normalize_backend(
            backend or worker.get("backend") or "core-agent"
        )
        if (
            selected_backend in {"core-agent", "codex", "gemini-cli"}
            and normalized_source in {"user", "user_cmd", "user_chat"}
            and self._looks_like_shell_command(instruction)
        ):
            selected_backend = "shell"
        backend_allowed, backend_detail = tool_access_store.is_backend_allowed(
            worker_id=str(worker.get("id") or worker_id),
            backend=selected_backend,
        )
        if not backend_allowed:
            msg = (
                f"backend `{selected_backend}` is blocked by worker tool policy "
                f"({backend_detail.get('reason')})."
            )
            await worker_task_store.update_task(
                task_id,
                status="failed",
                error=msg,
                ended_at=_now_iso(),
                result_summary=msg,
                retry_count=0,
            )
            await worker_registry.update_worker(
                worker["id"],
                status="ready",
                last_error=msg,
                last_task_id=task_id,
            )
            return {
                "ok": False,
                "error": "policy_blocked",
                "summary": msg,
                "task_id": task_id,
                "backend": selected_backend,
                "runtime_mode": self.runtime_mode,
            }
        workspace = self._resolve_runtime_workspace(worker)
        workspace.mkdir(parents=True, exist_ok=True)

        if selected_backend == "core-agent":
            core_result = await self._execute_core_agent_task(
                worker_id=str(worker.get("id") or worker_id),
                instruction=instruction,
                metadata=metadata or {},
            )
            ok = bool(core_result.get("ok"))
            summary = str(core_result.get("summary") or "")[:500]
            combined = str(core_result.get("result") or "")
            err = str(core_result.get("error") or "")
            await worker_task_store.update_task(
                task_id,
                status="done" if ok else "failed",
                result=combined,
                result_summary=summary or ("core-agent done" if ok else "core-agent failed"),
                error="" if ok else err or "core_agent_failed",
                ended_at=_now_iso(),
                retry_count=0,
            )
            await worker_registry.update_worker(
                worker["id"],
                status="ready",
                last_task_id=task_id,
                last_error="" if ok else (err or "core_agent_failed"),
            )
            return {
                "ok": ok,
                "task_id": task_id,
                "backend": "core-agent",
                "runtime_mode": self.runtime_mode,
                "summary": summary or combined[:500],
                "result": combined,
                "error": "" if ok else (err or "core_agent_failed"),
            }

        cmd, args = self._build_command(selected_backend, instruction)

        run = await self._execute_command(
            cmd=cmd,
            args=args,
            workspace=workspace,
            timeout_sec=self.exec_timeout_sec,
        )
        combined = self._combine_output(run.get("stdout"), run.get("stderr"))

        if self._should_retry_codex_without_instruction_flag(
            backend=selected_backend,
            output=combined,
        ):
            fallback_args = shlex.split(f"exec {shlex.quote(str(instruction or '').strip())}")
            rerun = await self._execute_command(
                cmd=self.codex_cmd,
                args=fallback_args,
                workspace=workspace,
                timeout_sec=self.exec_timeout_sec,
            )
            run = rerun
            combined = self._combine_output(run.get("stdout"), run.get("stderr"))

        if str(run.get("error")) == "prepare_failed":
            if self.fallback_to_core_agent and selected_backend in {"codex", "gemini-cli"}:
                core_result = await self._execute_core_agent_task(
                    worker_id=str(worker.get("id") or worker_id),
                    instruction=instruction,
                    metadata=metadata or {},
                )
                if core_result.get("ok"):
                    combined = str(core_result.get("result") or "")
                    summary = str(core_result.get("summary") or combined[:500])
                    summary = f"[fallback->core-agent] {summary}"[:500]
                    await worker_task_store.update_task(
                        task_id,
                        status="done",
                        result=combined,
                        result_summary=summary,
                        error="",
                        ended_at=_now_iso(),
                        retry_count=1,
                    )
                    await worker_registry.update_worker(
                        worker["id"],
                        status="ready",
                        last_task_id=task_id,
                        last_error="",
                    )
                    return {
                        "ok": True,
                        "task_id": task_id,
                        "backend": "core-agent",
                        "runtime_mode": self.runtime_mode,
                        "summary": summary,
                        "result": combined,
                        "fallback_from_backend": selected_backend,
                    }
            msg = str(run.get("message") or "worker execution prepare failed")
            await worker_task_store.update_task(
                task_id,
                status="failed",
                error=msg,
                ended_at=_now_iso(),
                result_summary=msg,
                retry_count=1 if selected_backend == "codex" else 0,
            )
            await worker_registry.update_worker(
                worker["id"],
                status="ready",
                last_error=msg,
                last_task_id=task_id,
            )
            return {
                "ok": False,
                "error": "exec_prepare_failed",
                "summary": msg,
                "task_id": task_id,
                "backend": selected_backend,
                "runtime_mode": self.runtime_mode,
            }

        if str(run.get("error")) == "timeout":
            msg = str(run.get("message") or f"Worker task timeout after {self.exec_timeout_sec}s")
            await worker_task_store.update_task(
                task_id,
                status="failed",
                error=msg,
                ended_at=_now_iso(),
                result_summary=msg,
                retry_count=1 if selected_backend == "codex" else 0,
            )
            await worker_registry.update_worker(
                worker["id"],
                status="ready",
                last_error=msg,
                last_task_id=task_id,
            )
            return {
                "ok": False,
                "error": "timeout",
                "summary": msg,
                "task_id": task_id,
                "backend": selected_backend,
                "runtime_mode": self.runtime_mode,
            }

        exit_code = int(run.get("exit_code", -1))
        ok = bool(run.get("ok"))
        summary = (combined[:500] or f"{selected_backend} exited with code {exit_code}").strip()
        await worker_task_store.update_task(
            task_id,
            status="done" if ok else "failed",
            result=combined,
            result_summary=summary,
            error="" if ok else f"exit_code={exit_code}",
            ended_at=_now_iso(),
            retry_count=1 if selected_backend == "codex" else 0,
        )
        await worker_registry.update_worker(
            worker["id"],
            status="ready",
            last_task_id=task_id,
            last_error="" if ok else f"exit_code={exit_code}",
        )
        return {
            "ok": ok,
            "task_id": task_id,
            "backend": selected_backend,
            "runtime_mode": self.runtime_mode,
            "summary": summary,
            "result": combined,
            "exit_code": exit_code,
        }


worker_runtime = WorkerRuntime()
