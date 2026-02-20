import logging
from typing import List
from google.genai import types

from core.skill_loader import skill_loader

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Registry for converting System Capabilities (Native Intents + Skills)
    into Gemini Function Declarations (Tools).
    """

    def get_all_tools(self) -> List[types.FunctionDeclaration]:
        """
        Get all available tools for the Agent.
        """
        tools = []

        # Skill Invocation Tool (Unified)
        # All capabilities including skill management are accessed via call_skill
        tools.append(self._get_skill_tool())

        return tools

    def get_specific_skill_tools(
        self, skills: List[dict]
    ) -> List[types.FunctionDeclaration]:
        """
        Generate explicit FunctionDeclarations for a list of specific skills.
        This allows the LLM to see "rss_subscribe(url=...)" instead of just "call_skill(name='rss_subscribe', ...)"
        """
        tools = []
        for skill in skills:
            name = skill["name"]
            # Sanitized name for tool (needs to be valid python identifier roughly)
            tool_name = f"skill_{name.replace('-', '_')}"

            # Simple schema: just instruction?
            # Or should we try to parse params?
            # For now, consistent with unified interface: just instruction.
            # But making it a separate tool name helps the LLM distinguish capabilities.

            desc = f"Invoke the '{name}' skill. Description: {skill.get('description', '')}"

            tools.append(
                types.FunctionDeclaration(
                    name=tool_name,
                    description=desc,
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "instruction": types.Schema(
                                type=types.Type.STRING,
                                description=f"Instruction for {name} skill",
                            )
                        },
                        required=["instruction"],
                    ),
                )
            )
        return tools

    def _get_skill_tool(self) -> types.FunctionDeclaration:
        """
        Unified tool to call installed skills.
        Dynamically builds description based on available skills and configuration.
        """
        from core.config import SKILL_INJECTION_MODE

        mode = SKILL_INJECTION_MODE.lower()

        if mode == "search_first":
            # 极简模式：不提供列表，强制 AI 搜索
            skill_desc = (
                "Call an installed skill (dynamic tool). "
                "The system has many capabilities installed. "
                "If you need to perform an action not covered by native tools, "
                "FIRST use `call_skill(skill_name='skill_manager', action='search', ...)` to find the capability, "
                "THEN use `call_skill` with the specific skill name."
            )

        elif mode == "compact":
            # 紧凑模式：只提供名称 (节省 Token)
            skills = skill_loader.get_skills_summary()
            if not skills:
                skill_desc = "No skills installed."
            else:
                skill_list_str = "\n".join([f"- {s['name']}" for s in skills])
                skill_desc = f"Call an installed skill. Available skills:\n{skill_list_str}\n(Descriptions omitted. Infer usage from name.)"

        else:
            # 默认完整模式 (full)
            skills = skill_loader.get_skills_summary()
            if not skills:
                skill_desc = "No skills installed."
            else:
                skill_list_str = "\n".join(
                    [f"- {s['name']}: {s['description'][:500]}" for s in skills]
                )
                skill_desc = (
                    f"Call an installed skill. Available skills:\n{skill_list_str}"
                )

        return types.FunctionDeclaration(
            name="call_skill",
            description=skill_desc,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "skill_name": types.Schema(
                        type=types.Type.STRING,
                        description="The exact name of the skill to call",
                    ),
                    "instruction": types.Schema(
                        type=types.Type.STRING,
                        description="The user's natural language instruction for the skill",
                    ),
                },
                required=["skill_name", "instruction"],
            ),
        )


tool_registry = ToolRegistry()
