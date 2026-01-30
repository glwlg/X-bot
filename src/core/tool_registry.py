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
        
        # 1. Native Tools (reserved for future use)
        tools.extend(self._get_native_tools())
        
        # 2. Skill Invocation Tool (Unified)
        # All capabilities including skill management are accessed via call_skill
        tools.append(self._get_skill_tool())
        
        # 3. Evolution Tool (Self-Evolution)
        tools.append(self._get_evolution_tool())
        
        return tools

    def _get_native_tools(self) -> List[types.FunctionDeclaration]:
        """Define native system capabilities"""
        # All native capabilities have been migrated to Skills.
        # We keep this method for future extension but return empty for now.
        return []


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
                skill_list_str = "\n".join([
                    f"- {s['name']}" 
                    for s in skills
                ])
                skill_desc = f"Call an installed skill. Available skills:\n{skill_list_str}\n(Descriptions omitted. Infer usage from name.)"

        else:
            # 默认完整模式 (full)
            skills = skill_loader.get_skills_summary()
            if not skills:
                skill_desc = "No skills installed."
            else:
                skill_list_str = "\n".join([
                    f"- {s['name']}: {s['description'][:100]}" 
                    for s in skills
                ])
                skill_desc = f"Call an installed skill. Available skills:\n{skill_list_str}"
            
        return types.FunctionDeclaration(
            name="call_skill",
            description=skill_desc,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "skill_name": types.Schema(type=types.Type.STRING, description="The exact name of the skill to call"),
                    "instruction": types.Schema(type=types.Type.STRING, description="The user's natural language instruction for the skill"),
                },
                required=["skill_name", "instruction"]
            )
        )


    
    def _get_evolution_tool(self) -> types.FunctionDeclaration:
        """
        Tool for Self-Evolution (Capability Expansion).
        """
        return types.FunctionDeclaration(
            name="evolve_capability",
            description=(
                "Call this tool when the user asks for a capability or task that you currently CANNOT perform with existing skills, "
                "OR when the task requires real-time programmatic verification (e.g., SSL checks, API testing) rather than just information retrieval. "
                "This tool will trigger the self-evolution process to analyze the request and write new skill code to achieve it."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "user_request": types.Schema(type=types.Type.STRING, description="The original user request describing the desired capability"),
                },
                required=["user_request"]
            )
        )

tool_registry = ToolRegistry()

