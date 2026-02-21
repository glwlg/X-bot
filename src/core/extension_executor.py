import inspect
import asyncio
import logging
import os
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, cast

from .config import GEMINI_MODEL, openai_async_client
from .skill_loader import skill_loader
from .tool_registry import tool_registry
from services.ai_service import AiService

logger = logging.getLogger(__name__)
EXTENSION_EXEC_TIMEOUT_SEC = int(os.getenv("EXTENSION_EXEC_TIMEOUT_SEC", "600"))
EXTENSION_MAX_FILES = int(os.getenv("EXTENSION_MAX_FILES", "8"))
EXTENSION_MAX_FILE_BYTES = int(
    os.getenv("EXTENSION_MAX_FILE_BYTES", str(5 * 1024 * 1024))
)
WORKFLOW_SKILL_TOOL_MAX_CHARS = int(os.getenv("WORKFLOW_SKILL_TOOL_MAX_CHARS", "12000"))


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
    missing_fields: List[str] = field(default_factory=list)

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
            if self.missing_fields:
                payload["missing_fields"] = list(self.missing_fields)
            return {
                "ok": True,
                "skill": self.skill_name,
                "text": self.text,
                "ui": ui_payload,
                "payload": payload,
                "data": self.data,
                "missing_fields": list(self.missing_fields),
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
            "missing_fields": list(self.missing_fields),
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
        valid, message, missing_fields = self._validate_args(args, schema)
        if not valid:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="invalid_args",
                message=message,
                missing_fields=missing_fields,
                failure_mode="recoverable",
            )

        allowed_tool_rules = self._parse_allowed_tool_rules(skill)
        if allowed_tool_rules:
            workflow_result = await self._run_standard_skill_with_tools(
                skill=skill,
                skill_name=skill_name,
                args=args,
                ctx=ctx,
                runtime=runtime,
                allowed_tool_rules=allowed_tool_rules,
            )
            if workflow_result is not None:
                return workflow_result

        entrypoint = skill.get("entrypoint", "scripts/execute.py")
        script_name = entrypoint.split("/")[-1]
        module = skill_loader.import_skill_module(skill_name, script_name)
        if not module or not hasattr(module, "execute"):
            workflow_result = await self._run_workflow_only_skill(
                skill=skill,
                skill_name=skill_name,
                args=args,
                ctx=ctx,
            )
            if workflow_result is not None:
                return workflow_result
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="entrypoint_missing",
                message=f"Skill {skill_name} entrypoint not found: {entrypoint}",
                failure_mode="recoverable",
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
                missing_fields = self._extract_missing_fields(result)
                return ExtensionRunResult(
                    ok=False,
                    skill_name=skill_name,
                    text=text,
                    files=files,
                    data=result,
                    error_code="skill_failed",
                    message=text or "Skill execution failed",
                    failure_mode=failure_mode,
                    missing_fields=missing_fields,
                )

            if self._looks_like_failure_text(text):
                return ExtensionRunResult(
                    ok=False,
                    skill_name=skill_name,
                    text=text,
                    files=files,
                    data=result,
                    error_code="skill_failed",
                    message=text or "Skill execution failed",
                    failure_mode="recoverable",
                    missing_fields=self._extract_missing_fields(result),
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
    ) -> tuple[bool, str, List[str]]:
        if not isinstance(args, dict):
            return False, "arguments must be an object", []

        required = schema.get("required") or []
        properties = schema.get("properties") or {}
        missing_fields: List[str] = []

        for field in required:
            if field not in args:
                missing_fields.append(str(field))
                continue
            value = args.get(field)
            if value is None:
                missing_fields.append(str(field))
                continue
            if isinstance(value, str) and not value.strip():
                missing_fields.append(str(field))
                continue
            if isinstance(value, (list, dict)) and not value:
                missing_fields.append(str(field))

        if missing_fields:
            joined = ", ".join(missing_fields)
            return False, f"missing required field(s): {joined}", missing_fields

        for key, value in args.items():
            prop = properties.get(key)
            if not prop:
                continue
            expected_type = prop.get("type")
            if not expected_type:
                continue
            if not self._matches_type(value, expected_type):
                return False, f"field '{key}' should be {expected_type}", []

        return True, "", []

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

    def _parse_allowed_tool_rules(
        self, skill: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        raw = skill.get("allowed_tools")
        if not isinstance(raw, list):
            return {}

        rules: Dict[str, Dict[str, Any]] = {}
        for item in raw:
            text = str(item or "").strip()
            if not text:
                continue
            match = re.match(r"^([A-Za-z_][\w\-]*)(?:\(([^)]*)\))?$", text)
            if not match:
                continue
            token = str(match.group(1) or "").strip().lower()
            if token in {"bash", "shell"}:
                tool_name = "bash"
            elif token in {"read", "write", "edit"}:
                tool_name = token
            else:
                continue

            rule = rules.get(tool_name)
            if not isinstance(rule, dict):
                rule = {"tool_name": tool_name, "bash_prefixes": []}
                rules[tool_name] = rule

            raw_scope = str(match.group(2) or "").strip()
            if tool_name != "bash" or not raw_scope:
                continue
            prefixes = list(rule.get("bash_prefixes") or [])
            for part in raw_scope.split(","):
                candidate = str(part or "").strip()
                if not candidate:
                    continue
                if ":" in candidate:
                    candidate = candidate.split(":", 1)[0].strip()
                if candidate and candidate not in prefixes:
                    prefixes.append(candidate)
            rule["bash_prefixes"] = prefixes

        return rules

    async def _run_standard_skill_with_tools(
        self,
        *,
        skill: Dict[str, Any],
        skill_name: str,
        args: Dict[str, Any],
        ctx: Any,
        runtime: Any,
        allowed_tool_rules: Dict[str, Dict[str, Any]],
    ) -> ExtensionRunResult | None:
        if openai_async_client is None:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="workflow_tools_unavailable",
                message="Model client is unavailable for standard skill execution",
                failure_mode="recoverable",
            )

        core_tools = {
            str(item.get("name") or ""): item
            for item in tool_registry.get_core_tools()
            if isinstance(item, dict)
        }
        tools: list[Dict[str, Any]] = []
        for tool_name in list(allowed_tool_rules.keys()):
            declaration = core_tools.get(tool_name)
            if isinstance(declaration, dict):
                tools.append(declaration)

        if not tools:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="workflow_tools_unavailable",
                message="No executable tools declared in allowed-tools",
                failure_mode="recoverable",
            )

        workflow = str(skill.get("skill_md_content") or "").strip()
        user_text = str(
            getattr(getattr(ctx, "message", None), "text", "") or ""
        ).strip()
        requested = user_text or str(args.get("query") or "").strip()
        allowed_desc = ", ".join(list(allowed_tool_rules.keys()))
        system_instruction = (
            "You are executing a standard SKILL.md workflow. "
            f"Only use allowed tools: {allowed_desc}. "
            "Do not call tools outside that set. "
            "When task is complete, produce concise Chinese final answer."
        )
        workflow_payload = (
            f"skill_name: {skill_name}\n"
            f"allowed_tools: {json.dumps(list(allowed_tool_rules.values()), ensure_ascii=False)}\n"
            f"args: {json.dumps(args or {}, ensure_ascii=False)}\n"
            f"user_request: {requested}\n\n"
            "SKILL.md workflow:\n"
            f"{workflow[:WORKFLOW_SKILL_TOOL_MAX_CHARS]}"
        )
        message_history = [
            {"role": "user", "parts": [{"text": workflow_payload}]},
        ]

        async def _tool_executor(
            name: str, tool_args: Dict[str, Any]
        ) -> Dict[str, Any]:
            return await self._execute_standard_tool_call(
                runtime=runtime,
                tool_name=name,
                tool_args=tool_args,
                allowed_tool_rules=allowed_tool_rules,
            )

        service = AiService()
        chunks: list[str] = []
        try:
            async for segment in service.generate_response_stream(
                message_history=message_history,
                tools=tools,
                tool_executor=_tool_executor,
                system_instruction=system_instruction,
            ):
                rendered = str(segment or "")
                if rendered:
                    chunks.append(rendered)
        except Exception as exc:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="workflow_failed",
                message=f"Standard skill execution failed: {exc}",
                failure_mode="recoverable",
            )

        text = "".join(chunks).strip()
        if not text:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="workflow_failed",
                message="Standard skill produced empty output",
                failure_mode="recoverable",
            )

        return ExtensionRunResult(
            ok=True,
            skill_name=skill_name,
            text=text,
            data={"workflow_only": True, "standard_skill": True},
        )

    async def _execute_standard_tool_call(
        self,
        *,
        runtime: Any,
        tool_name: str,
        tool_args: Dict[str, Any],
        allowed_tool_rules: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        name = str(tool_name or "").strip()
        if name not in allowed_tool_rules:
            return {
                "ok": False,
                "error_code": "policy_blocked",
                "message": f"tool not allowed by skill policy: {name}",
            }

        runtime_fn = getattr(runtime, name, None)
        if not callable(runtime_fn):
            return {
                "ok": False,
                "error_code": "runtime_tool_missing",
                "message": f"runtime tool unavailable: {name}",
            }

        params = dict(tool_args or {})
        if name in {"write", "edit", "bash"}:
            params.setdefault("execution_policy", "worker_execution_policy")

        if name == "bash":
            rule = allowed_tool_rules.get("bash") or {}
            prefixes = [
                str(item).strip()
                for item in list(rule.get("bash_prefixes") or [])
                if str(item).strip()
            ]
            command = str(params.get("command") or "").strip()
            if not command:
                return {
                    "ok": False,
                    "error_code": "invalid_args",
                    "message": "bash command is required",
                }
            if prefixes and not any(command.startswith(prefix) for prefix in prefixes):
                return {
                    "ok": False,
                    "error_code": "policy_blocked",
                    "message": "bash command not allowed by skill policy",
                }

        try:
            result = runtime_fn(**params)
            if inspect.isawaitable(result):
                result = await cast(Any, result)
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": str(result)}
        except Exception as exc:
            return {
                "ok": False,
                "error_code": "runtime_tool_failed",
                "message": str(exc),
            }

    async def _run_workflow_only_skill(
        self,
        *,
        skill: Dict[str, Any],
        skill_name: str,
        args: Dict[str, Any],
        ctx: Any,
    ) -> ExtensionRunResult | None:
        workflow = str(skill.get("skill_md_content") or "").strip()
        if not workflow:
            return None
        if openai_async_client is None:
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="entrypoint_missing",
                message=f"Skill {skill_name} has no executable entrypoint",
                failure_mode="recoverable",
            )
        client = cast(Any, openai_async_client)

        user_text = str(getattr(getattr(ctx, "message", None), "text", "") or "")
        schema = skill.get("input_schema") or {"type": "object", "properties": {}}
        prompt = (
            "You are a skill workflow executor. Follow the provided SKILL.md workflow "
            "and produce JSON only with keys: success (boolean), text (string), "
            "missing_fields (array), failure_mode (recoverable|fatal)."
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"skill_name: {skill_name}\n"
                    f"input_schema: {json.dumps(schema, ensure_ascii=False)}\n"
                    f"args: {json.dumps(args or {}, ensure_ascii=False)}\n"
                    f"user_request: {user_text}\n"
                    f"skill_workflow_markdown:\n{workflow[:12000]}"
                ),
            },
        ]

        response = None
        try:
            response = await client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except Exception:
            try:
                response = await client.chat.completions.create(
                    model=GEMINI_MODEL,
                    messages=messages,
                    temperature=0,
                )
            except Exception as exc:
                return ExtensionRunResult(
                    ok=False,
                    skill_name=skill_name,
                    error_code="entrypoint_missing",
                    message=f"Skill {skill_name} has no executable entrypoint: {exc}",
                    failure_mode="recoverable",
                )

        content = ""
        choices = list(getattr(response, "choices", []) or [])
        if choices:
            message = getattr(choices[0], "message", None)
            content = str(getattr(message, "content", "") or "")
        payload = self._extract_json_object(content)
        if not payload:
            payload = {
                "success": False,
                "text": content[:500],
                "failure_mode": "recoverable",
            }

        if payload.get("success") is False:
            missing = payload.get("missing_fields")
            missing_fields = missing if isinstance(missing, list) else []
            return ExtensionRunResult(
                ok=False,
                skill_name=skill_name,
                error_code="workflow_failed",
                message=str(payload.get("text") or "Workflow execution failed"),
                text=str(payload.get("text") or ""),
                failure_mode=str(payload.get("failure_mode") or "recoverable"),
                missing_fields=[str(item) for item in missing_fields],
                data={"workflow_only": True},
            )

        return ExtensionRunResult(
            ok=True,
            skill_name=skill_name,
            text=str(payload.get("text") or ""),
            data={"workflow_only": True},
        )

    def _extract_json_object(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        candidates = [raw]
        fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.I)
        candidates.extend(fenced)
        for candidate in candidates:
            try:
                loaded = json.loads(candidate)
            except Exception:
                continue
            if isinstance(loaded, dict):
                return loaded
        return {}

    def _extract_missing_fields(self, result: Dict[str, Any]) -> List[str]:
        missing = result.get("missing_fields")
        if isinstance(missing, list):
            return [str(item) for item in missing if str(item or "").strip()]

        message = str(result.get("message") or result.get("text") or "")
        matched = re.findall(r"missing required field(?:s)?:\s*([\w,\s_-]+)", message)
        if not matched:
            return []
        fields: List[str] = []
        for token in re.split(r"[,\s]+", matched[0]):
            field = str(token or "").strip()
            if field and field not in fields:
                fields.append(field)
        return fields

    def _looks_like_failure_text(self, text: str) -> bool:
        rendered = str(text or "").strip()
        if not rendered:
            return False

        if rendered.startswith("✅"):
            return False

        lowered = rendered.lower()
        if lowered.startswith(("❌", "error", "failed", "invalid", "missing")):
            return True

        preview = lowered[:250]
        patterns = (
            "missing required",
            "请提供",
            "缺少",
            "无法",
            "未配置",
            "not found",
            "not configured",
            "工具调用轮次已达上限",
            "已达到单工具调用上限",
            "语义上重复的工具调用",
            "max tool-loop turns",
            "tool_budget_guard",
            "semantic_loop_guard",
        )
        return any(token in preview for token in patterns)
