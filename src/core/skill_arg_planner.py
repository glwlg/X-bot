from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, cast

from core.config import get_client_for_model
from core.model_config import select_model_for_role
from extension.skills.registry import skill_registry as skill_loader
from services.openai_adapter import create_chat_completion, extract_text_from_chat_completion

logger = logging.getLogger(__name__)

# Backward-compatible async client injection for tests/legacy callers.
openai_async_client: Any = None


def _resolve_planner_client(model_name: str) -> Any:
    if openai_async_client is not None:
        return openai_async_client
    return get_client_for_model(model_name, is_async=True)

SKILL_ARG_PLANNER_ENABLED = (
    os.getenv("SKILL_ARG_PLANNER_ENABLED", "true").strip().lower() == "true"
)
SKILL_ARG_PLANNER_MAX_MD_CHARS = int(
    os.getenv("SKILL_ARG_PLANNER_MAX_MD_CHARS", "6000")
)


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def _normalize_schema(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}
    properties = schema.get("properties")
    required = schema.get("required")
    normalized = dict(schema)
    normalized["properties"] = dict(properties) if isinstance(properties, dict) else {}
    normalized["required"] = list(required) if isinstance(required, list) else []
    if "type" not in normalized:
        normalized["type"] = "object"
    return normalized


def _missing_required_fields(args: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    required = list(schema.get("required") or [])
    missing: List[str] = []
    for field in required:
        key = str(field or "").strip()
        if not key:
            continue
        if key not in args:
            missing.append(key)
            continue
        value = args.get(key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(key)
            continue
        if isinstance(value, (list, dict)) and not value:
            missing.append(key)
    return missing


def _extract_json_object(text: str) -> Dict[str, Any]:
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


class SkillArgPlanner:
    async def plan(
        self,
        *,
        skill_name: str,
        current_args: Dict[str, Any] | None,
        user_request: str,
        validation_error: str = "",
        force: bool = False,
    ) -> Dict[str, Any]:
        base_args = dict(current_args or {})
        skill = skill_loader.get_skill(str(skill_name or "")) or {}
        schema = _normalize_schema(skill.get("input_schema"))
        missing = _missing_required_fields(base_args, schema)

        should_plan = bool(
            SKILL_ARG_PLANNER_ENABLED and (force or missing or not base_args)
        )
        if not should_plan or not SKILL_ARG_PLANNER_ENABLED:
            return {
                "args": base_args,
                "missing_fields": missing,
                "planned": False,
                "source": "direct",
                "reason": "",
            }

        planned = await self._plan_with_model(
            skill_name=str(skill_name or ""),
            skill=skill,
            schema=schema,
            user_request=user_request,
            current_args=base_args,
            validation_error=validation_error,
        )
        llm_args = planned.get("args")
        llm_args = llm_args if isinstance(llm_args, dict) else {}

        merged = dict(base_args)
        for key, value in llm_args.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            if force or _is_empty_value(merged.get(key_text)):
                merged[key_text] = value

        missing_fields = _missing_required_fields(merged, schema)
        used_llm = bool(planned)
        if used_llm and not missing_fields:
            reported_missing = planned.get("missing_fields")
            if isinstance(reported_missing, list):
                missing_fields = [
                    str(item or "").strip()
                    for item in reported_missing
                    if str(item or "").strip()
                ]

        return {
            "args": merged,
            "missing_fields": missing_fields,
            "planned": used_llm,
            "source": "llm" if used_llm else "direct",
            "reason": str(planned.get("reason") or "").strip() if used_llm else "",
        }

    async def _plan_with_model(
        self,
        *,
        skill_name: str,
        skill: Dict[str, Any],
        schema: Dict[str, Any],
        user_request: str,
        current_args: Dict[str, Any],
        validation_error: str,
    ) -> Dict[str, Any]:
        model_to_use = select_model_for_role("primary")
        client = cast(Any, _resolve_planner_client(model_to_use))
        if client is None:
            return {}

        skill_markdown = str(skill.get("skill_md_content") or "")
        if len(skill_markdown) > SKILL_ARG_PLANNER_MAX_MD_CHARS:
            skill_markdown = skill_markdown[:SKILL_ARG_PLANNER_MAX_MD_CHARS]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict skill argument planner. "
                    "Return JSON only with keys: args (object), missing_fields (array), reason (string)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"skill_name: {skill_name}\n"
                    f"skill_description: {str(skill.get('description') or '')}\n"
                    f"input_schema: {json.dumps(schema, ensure_ascii=False)}\n"
                    f"current_args: {json.dumps(current_args, ensure_ascii=False)}\n"
                    f"validation_error: {validation_error}\n"
                    f"user_request: {str(user_request or '').strip()}\n"
                    f"skill_workflow_markdown:\n{skill_markdown}"
                ),
            },
        ]

        request_kwargs: Dict[str, Any] = {
            "model": model_to_use,
            "messages": messages,
            "temperature": 0,
        }
        try:
            response = await create_chat_completion(
                async_client=client,
                **request_kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            try:
                response = await create_chat_completion(
                    async_client=client,
                    **request_kwargs,
                )
            except Exception as exc:
                logger.debug("Skill arg planner failed for %s: %s", skill_name, exc)
                return {}

        return _extract_json_object(extract_text_from_chat_completion(response))


skill_arg_planner = SkillArgPlanner()
