import inspect
import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.skill_loader import skill_loader

logger = logging.getLogger(__name__)
EXTENSION_EXEC_TIMEOUT_SEC = int(os.getenv("EXTENSION_EXEC_TIMEOUT_SEC", "600"))
EXTENSION_MAX_FILES = int(os.getenv("EXTENSION_MAX_FILES", "8"))
EXTENSION_MAX_FILE_BYTES = int(
    os.getenv("EXTENSION_MAX_FILE_BYTES", str(5 * 1024 * 1024))
)


@dataclass
class ExtensionRunResult:
    ok: bool
    skill_name: str
    text: str = ""
    files: Dict[str, bytes] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    message: str = ""
    failure_mode: str = ""

    def to_tool_response(self) -> Dict[str, Any]:
        task_outcome = str(self.data.get("task_outcome") or "").strip().lower()
        terminal = bool(self.data.get("terminal")) or task_outcome == "done"
        ui_payload = self.data.get("ui") if isinstance(self.data, dict) else None
        if not isinstance(ui_payload, dict):
            ui_payload = {}
        if (
            self.ok
            and not terminal
            and not task_outcome
            and isinstance(ui_payload.get("actions"), list)
        ):
            terminal = True
            task_outcome = "done"

        if self.ok:
            payload: Dict[str, Any] = {"text": self.text}
            if ui_payload:
                payload["ui"] = ui_payload
            return {
                "ok": True,
                "skill": self.skill_name,
                "text": self.text,
                "ui": ui_payload,
                "payload": payload,
                "data": self.data,
                "terminal": terminal,
                "task_outcome": task_outcome or ("done" if terminal else ""),
                "summary": self.text[:200]
                if self.text
                else f"Extension {self.skill_name} executed",
            }

        # Preserve terminal semantics from extension payload, so orchestrator can
        # close the task on deterministic failure/partial outcomes.
        if not terminal and task_outcome in {"failed", "partial"}:
            terminal = True
        if not task_outcome and terminal:
            task_outcome = "failed"
        summary = (self.message or self.text or f"Extension {self.skill_name} failed")[
            :200
        ]
        failure_mode = (self.failure_mode or "").strip().lower()
        if failure_mode not in {"recoverable", "fatal"}:
            failure_mode = "recoverable"
        return {
            "ok": False,
            "skill": self.skill_name,
            "error_code": self.error_code or "extension_failed",
            "message": self.message or "Extension execution failed",
            "text": self.text or self.message or "",
            "ui": ui_payload,
            "payload": {
                "text": self.text or self.message or "",
                "ui": ui_payload,
            },
            "data": self.data,
            "terminal": terminal,
            "task_outcome": task_outcome,
            "summary": summary,
            "failure_mode": failure_mode,
        }


class ExtensionExecutor:
    """Deterministic extension executor with minimal schema validation."""

    async def execute(
        self, skill_name: str, args: Dict[str, Any], ctx: Any, runtime: Any
    ) -> ExtensionRunResult:
        skill = skill_loader.get_skill(skill_name)
        if not skill:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="skill_not_found",
                message=f"Skill not found: {skill_name}",
            )

        schema = skill.get("input_schema") or {"type": "object", "properties": {}}
        valid, message = self._validate_args(args, schema)
        if not valid:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="invalid_args",
                message=message,
            )

        entrypoint = skill.get("entrypoint", "scripts/execute.py")
        script_name = entrypoint.split("/")[-1]
        module = skill_loader.import_skill_module(skill_name, script_name)
        if not module or not hasattr(module, "execute"):
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="entrypoint_missing",
                message=f"Skill {skill_name} entrypoint not found: {entrypoint}",
            )

        try:
            result = await asyncio.wait_for(
                self._run_execute(module.execute, ctx, args, runtime),
                timeout=EXTENSION_EXEC_TIMEOUT_SEC,
            )
            return self._normalize_result(skill_name, result)
        except asyncio.TimeoutError:
            logger.warning(
                "Extension execution timeout: skill=%s timeout=%ss",
                skill_name,
                EXTENSION_EXEC_TIMEOUT_SEC,
            )
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="execution_timeout",
                message=f"Extension '{skill_name}' timed out after {EXTENSION_EXEC_TIMEOUT_SEC}s",
                failure_mode="recoverable",
            )
        except Exception as exc:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="execution_error",
                message=str(exc),
                failure_mode="recoverable",
            )

    async def _run_execute(
        self, execute_fn: Any, ctx: Any, args: Dict[str, Any], runtime: Any
    ) -> Any:
        if inspect.isasyncgenfunction(execute_fn):
            final_result: Any = None
            async for chunk in execute_fn(ctx, args, runtime):
                # Keep final structured payload if present, fallback to last non-empty chunk.
                if isinstance(chunk, dict) and (
                    "text" in chunk
                    or "ui" in chunk
                    or "files" in chunk
                    or "success" in chunk
                ):
                    final_result = chunk
                elif chunk is not None:
                    final_result = chunk
            return final_result

        if inspect.iscoroutinefunction(execute_fn):
            return await execute_fn(ctx, args, runtime)

        return execute_fn(ctx, args, runtime)

    def _normalize_result(self, skill_name: str, result: Any) -> ExtensionRunResult:
        if result is None:
            return ExtensionRunResult(ok=True, skill_name=skill_name, text="")

        if isinstance(result, str):
            return ExtensionRunResult(ok=True, skill_name=skill_name, text=result)

        if isinstance(result, dict):
            files = self._normalize_files(result.get("files") or {})
            text = result.get("text") or ""

            # Some skills return explicit status fields.
            if result.get("success") is False:
                failure_mode = str(result.get("failure_mode") or "").strip().lower()
                if failure_mode not in {"recoverable", "fatal"}:
                    failure_mode = "recoverable"
                return ExtensionRunResult(
                    ok=False,
                    skill_name=skill_name,
                    text=text,
                    files=files,
                    data=result,
                    error_code="skill_failed",
                    message=text or "Skill execution failed",
                    failure_mode=failure_mode,
                )

            return ExtensionRunResult(
                ok=True,
                skill_name=skill_name,
                text=text,
                files=files,
                data=result,
            )

        return ExtensionRunResult(ok=True, skill_name=skill_name, text=str(result))

    def _normalize_files(self, files: Any) -> Dict[str, bytes]:
        if not isinstance(files, dict):
            return {}

        normalized: Dict[str, bytes] = {}
        for idx, (name, content) in enumerate(files.items()):
            if idx >= EXTENSION_MAX_FILES:
                break
            filename = str(name or f"file_{idx + 1}")

            file_bytes: bytes
            if isinstance(content, bytes):
                file_bytes = content
            elif isinstance(content, bytearray):
                file_bytes = bytes(content)
            elif isinstance(content, str):
                file_bytes = content.encode("utf-8")
            else:
                continue

            if len(file_bytes) > EXTENSION_MAX_FILE_BYTES:
                continue
            normalized[filename] = file_bytes

        return normalized

    def _validate_args(
        self, args: Dict[str, Any], schema: Dict[str, Any]
    ) -> tuple[bool, str]:
        if not isinstance(args, dict):
            return False, "arguments must be an object"

        required = schema.get("required") or []
        properties = schema.get("properties") or {}

        for field in required:
            if field not in args:
                return False, f"missing required field: {field}"

        for key, value in args.items():
            prop = properties.get(key)
            if not prop:
                continue
            expected_type = prop.get("type")
            if not expected_type:
                continue
            if not self._matches_type(value, expected_type):
                return False, f"field '{key}' should be {expected_type}"

        return True, ""

    def _matches_type(self, value: Any, expected: str) -> bool:
        mapping = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        py_type = mapping.get(expected)
        if not py_type:
            return True
        if expected == "integer" and isinstance(value, bool):
            return False
        if expected == "number" and isinstance(value, bool):
            return False
        return isinstance(value, py_type)
