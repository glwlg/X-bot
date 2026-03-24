import re
from dataclasses import dataclass, field
from typing import List

from extension.skills.registry import skill_registry as skill_loader


@dataclass
class ExtensionCandidate:
    name: str
    description: str
    tool_name: str
    triggers: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)


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

            if bool(skill.get("manager_only")):
                continue

            description = skill.get("description", "")
            triggers = skill.get("triggers", []) or []
            allowed_tools = skill.get("allowed_tools", []) or []

            safe = name.replace("-", "_")

            results.append(
                ExtensionCandidate(
                    name=name,
                    description=description,
                    tool_name=f"ext_{safe}",
                    triggers=triggers,
                    allowed_tools=allowed_tools,
                )
            )

        return results[:max_candidates]
