import asyncio
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_BASH_TIMEOUT_SEC = 60
MAX_BASH_OUTPUT = 32_000


@dataclass
class PrimitiveRuntime:
    """Deterministic runtime for core file/shell primitives."""

    workspace_root: str | None = None

    def __post_init__(self) -> None:
        self.workspace_root = os.path.abspath(self.workspace_root or os.getcwd())
        self._kernel_protected_roots = self._build_kernel_protected_roots()

    def _ok(self, data: Any, summary: str) -> Dict[str, Any]:
        return {"ok": True, "data": data, "summary": summary}

    def _err(self, error_code: str, message: str) -> Dict[str, Any]:
        return {"ok": False, "error_code": error_code, "message": message}

    def _build_kernel_protected_roots(self) -> List[str]:
        roots: List[str] = []
        configured = os.getenv("KERNEL_PROTECTED_PATHS", "").strip()
        if configured:
            for item in configured.split(","):
                raw = item.strip()
                if not raw:
                    continue
                expanded = os.path.expanduser(raw)
                if not os.path.isabs(expanded):
                    expanded = os.path.abspath(os.path.join(self.workspace_root, expanded))
                roots.append(os.path.abspath(expanded))

        # Always include current core runtime source roots.
        current_file = Path(__file__).resolve()
        src_root = current_file.parents[1]  # .../src
        for rel in ("core", "services", "handlers"):
            roots.append(str((src_root / rel).resolve()))

        # Include container default path for safety.
        for static_path in ("/app/src/core", "/app/src/services", "/app/src/handlers"):
            roots.append(os.path.abspath(static_path))

        unique: List[str] = []
        for root in roots:
            if root not in unique:
                unique.append(root)
        return unique

    def _is_path_under(self, target: str, root: str) -> bool:
        try:
            target_abs = os.path.abspath(target)
            root_abs = os.path.abspath(root)
            common = os.path.commonpath([target_abs, root_abs])
            return common == root_abs
        except Exception:
            return False

    def _is_kernel_protected_path(self, target: str) -> bool:
        return any(self._is_path_under(target, root) for root in self._kernel_protected_roots)

    def _policy_block(self, message: str) -> Dict[str, Any]:
        return self._err("policy_blocked", message)

    def _resolve_path(self, path: str) -> str:
        if not path:
            raise ValueError("path is required")
        expanded = os.path.expanduser(path)
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.workspace_root, expanded)
        return os.path.abspath(expanded)

    async def read(
        self,
        path: str,
        start_line: int = 1,
        max_lines: int = 200,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        try:
            target = self._resolve_path(path)
        except Exception as exc:
            return self._err("invalid_path", str(exc))

        if start_line < 1:
            return self._err("invalid_args", "start_line must be >= 1")
        if max_lines < 1:
            return self._err("invalid_args", "max_lines must be >= 1")

        if not os.path.exists(target):
            return self._err("not_found", f"Path not found: {target}")
        if os.path.isdir(target):
            return self._err("is_directory", f"Path is a directory: {target}")

        try:
            with open(target, "r", encoding=encoding) as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            return self._err("decode_error", f"Failed to decode file as {encoding}")
        except Exception as exc:
            return self._err("read_failed", str(exc))

        total = len(lines)
        start_idx = start_line - 1
        end_idx = min(start_idx + max_lines, total)
        selected = lines[start_idx:end_idx]
        numbered = [f"{idx + 1:>5}: {line.rstrip()}" for idx, line in enumerate(selected, start=start_idx)]

        data = {
            "path": target,
            "start_line": start_idx + 1 if total else 1,
            "end_line": end_idx,
            "total_lines": total,
            "content": "\n".join(numbered),
        }
        return self._ok(data, f"Read {len(selected)} line(s) from {target}")

    async def write(
        self,
        path: str,
        content: str,
        mode: str = "overwrite",
        create_parents: bool = True,
        encoding: str = "utf-8",
        execution_policy: str = "worker_execution_policy",
    ) -> Dict[str, Any]:
        try:
            target = self._resolve_path(path)
        except Exception as exc:
            return self._err("invalid_path", str(exc))

        if execution_policy == "heartbeat_readonly_policy":
            return self._policy_block("heartbeat readonly mode forbids write operations")
        if self._is_kernel_protected_path(target):
            return self._policy_block(f"kernel-protected path is read-only: {target}")

        write_mode = mode.lower()
        if write_mode not in {"overwrite", "append"}:
            return self._err("invalid_args", "mode must be 'overwrite' or 'append'")

        parent = os.path.dirname(target)
        try:
            if create_parents and parent:
                os.makedirs(parent, exist_ok=True)
            elif parent and not os.path.exists(parent):
                return self._err("parent_missing", f"Parent directory does not exist: {parent}")

            file_mode = "w" if write_mode == "overwrite" else "a"
            with open(target, file_mode, encoding=encoding) as f:
                f.write(content)
        except Exception as exc:
            return self._err("write_failed", str(exc))

        return self._ok(
            {"path": target, "bytes_written": len(content.encode(encoding, errors="ignore")), "mode": write_mode},
            f"Wrote content to {target} ({write_mode})",
        )

    async def edit(
        self,
        path: str,
        edits: List[Dict[str, Any]],
        dry_run: bool = False,
        encoding: str = "utf-8",
        execution_policy: str = "worker_execution_policy",
    ) -> Dict[str, Any]:
        try:
            target = self._resolve_path(path)
        except Exception as exc:
            return self._err("invalid_path", str(exc))

        if execution_policy == "heartbeat_readonly_policy":
            return self._policy_block("heartbeat readonly mode forbids edit operations")
        if self._is_kernel_protected_path(target):
            return self._policy_block(f"kernel-protected path is read-only: {target}")

        if not isinstance(edits, list) or not edits:
            return self._err("invalid_args", "edits must be a non-empty list")
        if not os.path.exists(target):
            return self._err("not_found", f"Path not found: {target}")
        if os.path.isdir(target):
            return self._err("is_directory", f"Path is a directory: {target}")

        try:
            with open(target, "r", encoding=encoding) as f:
                original = f.read()
        except UnicodeDecodeError:
            return self._err("decode_error", f"Failed to decode file as {encoding}")
        except Exception as exc:
            return self._err("read_failed", str(exc))

        updated = original
        applied: List[Dict[str, Any]] = []

        for idx, item in enumerate(edits):
            if not isinstance(item, dict):
                return self._err("invalid_args", f"edit at index {idx} must be an object")

            old_text = item.get("old_text")
            new_text = item.get("new_text")
            replace_all = bool(item.get("replace_all", False))

            if old_text is None or new_text is None:
                return self._err("invalid_args", f"edit at index {idx} requires old_text and new_text")
            if old_text == "":
                return self._err("invalid_args", f"edit at index {idx} old_text cannot be empty")

            count_before = updated.count(old_text)
            if count_before == 0:
                return self._err("edit_not_found", f"edit at index {idx} old_text not found")

            if replace_all:
                updated = updated.replace(old_text, new_text)
                replaced = count_before
            else:
                updated = updated.replace(old_text, new_text, 1)
                replaced = 1

            applied.append(
                {
                    "index": idx,
                    "replace_all": replace_all,
                    "matches_found": count_before,
                    "replaced": replaced,
                }
            )

        changed = updated != original
        if changed and not dry_run:
            try:
                with open(target, "w", encoding=encoding) as f:
                    f.write(updated)
            except Exception as exc:
                return self._err("write_failed", str(exc))

        return self._ok(
            {
                "path": target,
                "applied_edits": applied,
                "changed": changed,
                "dry_run": dry_run,
            },
            f"Applied {len(applied)} edit(s) to {target}" + (" (dry-run)" if dry_run else ""),
        )

    async def bash(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int = DEFAULT_BASH_TIMEOUT_SEC,
        execution_policy: str = "worker_execution_policy",
    ) -> Dict[str, Any]:
        if not command or not command.strip():
            return self._err("invalid_args", "command is required")
        if execution_policy == "heartbeat_readonly_policy":
            return self._policy_block("heartbeat readonly mode forbids bash operations")

        workdir = None
        if cwd:
            try:
                workdir = self._resolve_path(cwd)
            except Exception as exc:
                return self._err("invalid_path", str(exc))
            if not os.path.exists(workdir):
                return self._err("not_found", f"cwd does not exist: {workdir}")
            if not os.path.isdir(workdir):
                return self._err("not_directory", f"cwd is not a directory: {workdir}")
            if self._is_kernel_protected_path(workdir):
                return self._policy_block(f"kernel-protected cwd is read-only: {workdir}")

        lowered_cmd = command.lower()
        for root in self._kernel_protected_roots:
            marker = root.lower()
            if marker and marker in lowered_cmd:
                return self._policy_block(f"command references kernel-protected path: {root}")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir or self.workspace_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(Exception):
                    await process.communicate()
                return self._err("timeout", f"Command timed out after {timeout_sec}s")
        except Exception as exc:
            return self._err("exec_failed", str(exc))

        out_text = (stdout or b"").decode("utf-8", errors="replace")
        err_text = (stderr or b"").decode("utf-8", errors="replace")
        combined = out_text
        if err_text:
            combined = f"{combined}\n[stderr]\n{err_text}" if combined else f"[stderr]\n{err_text}"

        if len(combined) > MAX_BASH_OUTPUT:
            combined = combined[:MAX_BASH_OUTPUT] + "\n...[truncated]"

        data = {
            "command": command,
            "cwd": workdir or self.workspace_root,
            "exit_code": process.returncode,
            "output": combined,
        }
        if process.returncode != 0:
            return {
                "ok": False,
                "error_code": "command_failed",
                "message": f"Command exited with code {process.returncode}",
                "data": data,
            }
        return self._ok(data, f"Command exited with code {process.returncode}")
