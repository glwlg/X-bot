from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, cast

from core.config import GEMINI_MODEL, openai_async_client
from core.skill_loader import skill_loader

logger = logging.getLogger(__name__)

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


def _extract_first_url(text: str) -> str:
    candidate = str(text or "")
    url_match = re.search(r"https?://[^\s)\]>]+", candidate)
    if url_match:
        return url_match.group(0)
    domain_match = re.search(
        r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s)\]>]*)?",
        candidate,
    )
    if domain_match:
        token = domain_match.group(0)
        if not token.lower().startswith("http"):
            return f"https://{token}"
    return ""


def _pick_action_enum(user_request: str, enum_values: List[str]) -> str:
    lowered = str(user_request or "").lower()
    rules = [
        (("summary", "summarize", "总结", "概括"), ("summary", "summarize", "摘要")),
        (
            ("visit", "browse", "open", "访问", "打开", "网页"),
            ("visit", "browse", "open"),
        ),
        (("search", "搜索", "查找", "find"), ("search", "query", "find")),
    ]
    for intent_tokens, action_tokens in rules:
        if not any(token in lowered for token in intent_tokens):
            continue
        for item in enum_values:
            value = str(item or "").strip().lower()
            if any(token in value for token in action_tokens):
                return str(item)
    return str(enum_values[0]) if enum_values else ""


def _seed_args_from_request(
    *,
    args: Dict[str, Any],
    schema: Dict[str, Any],
    user_request: str,
) -> Dict[str, Any]:
    seeded = dict(args)
    properties = dict(schema.get("properties") or {})
    request_text = str(user_request or "").strip()
    if not request_text:
        return seeded

    first_url = _extract_first_url(request_text)
    for key, prop in properties.items():
        name = str(key or "").strip()
        if not name:
            continue
        current = seeded.get(name)
        if not _is_empty_value(current):
            continue

        lowered_name = name.lower()
        expected = str((prop or {}).get("type") or "").lower()
        enum_values = list((prop or {}).get("enum") or [])

        if expected == "string" and lowered_name in {
            "url",
            "link",
            "source_url",
            "website",
        }:
            if first_url:
                seeded[name] = first_url
                continue

        if lowered_name in {
            "query",
            "question",
            "topic",
            "prompt",
            "instruction",
            "text",
        }:
            seeded[name] = request_text
            continue

        if lowered_name == "action" and enum_values:
            action = _pick_action_enum(
                request_text, [str(item) for item in enum_values]
            )
            if action:
                seeded[name] = action

    if len(properties) == 1:
        (single_name, single_prop), *_ = properties.items()
        if (
            _is_empty_value(seeded.get(single_name))
            and str((single_prop or {}).get("type") or "").lower() == "string"
        ):
            seeded[single_name] = request_text

    return seeded


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
        heuristic_args = _seed_args_from_request(
            args=base_args,
            schema=schema,
            user_request=user_request,
        )
        missing = _missing_required_fields(heuristic_args, schema)

        should_plan = bool(force or missing or not heuristic_args)
        if not should_plan or not SKILL_ARG_PLANNER_ENABLED:
            return {
                "args": heuristic_args,
                "missing_fields": missing,
                "planned": False,
                "source": "heuristic",
                "reason": "",
            }

        planned = await self._plan_with_model(
            skill_name=str(skill_name or ""),
            skill=skill,
            schema=schema,
            user_request=user_request,
            current_args=heuristic_args,
            validation_error=validation_error,
        )
        llm_args = planned.get("args")
        llm_args = llm_args if isinstance(llm_args, dict) else {}

        merged = dict(heuristic_args)
        for key, value in llm_args.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            if force or _is_empty_value(merged.get(key_text)):
                merged[key_text] = value

        missing_fields = _missing_required_fields(merged, schema)
        if not missing_fields:
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
            "planned": True,
            "source": "llm",
            "reason": str(planned.get("reason") or "").strip(),
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
        if openai_async_client is None:
            return {}
        client = cast(Any, openai_async_client)

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
            "model": GEMINI_MODEL,
            "messages": messages,
            "temperature": 0,
        }
        try:
            response = await client.chat.completions.create(
                **request_kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            try:
                response = await client.chat.completions.create(
                    **request_kwargs,
                )
            except Exception as exc:
                logger.debug("Skill arg planner failed for %s: %s", skill_name, exc)
                return {}

        content = ""
        choices = list(getattr(response, "choices", []) or [])
        if choices:
            message = getattr(choices[0], "message", None)
            content = str(getattr(message, "content", "") or "")
        return _extract_json_object(content)


skill_arg_planner = SkillArgPlanner()
