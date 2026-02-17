import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.skill_loader import skill_loader


@dataclass
class ExtensionCandidate:
    name: str
    description: str
    tool_name: str
    input_schema: Dict[str, Any]
    schema_summary: str
    triggers: List[str] = field(default_factory=list)


def _tokenize(text: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]+", text.lower())
        if len(tok) > 1
    }


class ExtensionRouter:
    """Simple candidate provider - LLM decides which extension to use."""

    def route(
        self, user_text: str, max_candidates: int = 10
    ) -> List[ExtensionCandidate]:
        """返回所有可用扩展的候选列表，让 LLM 决策选择"""
        if not user_text:
            return []

        all_skills = skill_loader.get_skills_summary()

        results: List[ExtensionCandidate] = []
        for skill in all_skills:
            name = skill.get("name", "")
            if not name:
                continue
            normalized_name = str(name).strip().lower()
            if normalized_name in {"file_manager", "local_file_manager"}:
                # Core primitives already provide direct read/write/edit/bash access.
                continue

            description = skill.get("description", "")
            triggers = skill.get("triggers", []) or []
            input_schema = skill.get("input_schema", {}) or {
                "type": "object",
                "properties": {},
            }

            safe = name.replace("-", "_")
            schema = input_schema
            props = list((schema.get("properties") or {}).keys())
            required = list(schema.get("required") or [])
            schema_summary = f"required={required}, fields={props[:8]}"

            results.append(
                ExtensionCandidate(
                    name=name,
                    description=description,
                    tool_name=f"ext_{safe}",
                    input_schema=schema,
                    schema_summary=schema_summary,
                    triggers=triggers,
                )
            )

        return results[:max_candidates]
