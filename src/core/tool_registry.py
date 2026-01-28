import logging
from typing import List, Dict, Any, Callable
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
        
        # 1. Native Tools
        tools.extend(self._get_native_tools())
        
        # 2. Skill Invocation Tool (Unified)
        tools.append(self._get_skill_tool())
        
        # 3. Discovery Tool
        tools.extend(self._get_discovery_tools())
        
        return tools

    def _get_native_tools(self) -> List[types.FunctionDeclaration]:
        """Define native system capabilities"""
        # All native capabilities have been migrated to Skills (e.g. rss_subscribe, stock_watch).
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
                "FIRST use `search_skill` or `list_skills` (if available) to find the capability, "
                "THEN use `call_skill` with the specific skill name."
            )
        
        elif mode == "compact":
            # 紧凑模式：只提供名称和简短触发词 (节省 Token)
            skills = skill_loader.get_skills_summary()
            if not skills:
                skill_desc = "No skills installed."
            else:
                skill_list_str = "\n".join([
                    f"- {s['name']}" 
                    for s in skills
                ])
                skill_desc = f"Call an installed skill. Available skills:\n{skill_list_str}\n(Descriptions omitted for brevity. Infer usage from name or search.)"

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



    def _get_discovery_tools(self) -> List[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name="search_skill",
                description="Search for a skill/tool in the marketplace when you lack a capability. Returns a list of candidates. DO NOT install automatically.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "query": types.Schema(type=types.Type.STRING, description="Search query"),
                    },
                    required=["query"]
                )
            ),
            types.FunctionDeclaration(
                name="install_skill",
                description="Install a specific skill. ONLY call this if the user has explicitly confirmed.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "repo_name": types.Schema(type=types.Type.STRING, description="The repository name (e.g. 'owner/repo')"),
                        "skill_name": types.Schema(type=types.Type.STRING, description="The skill name"),
                    },
                    required=["repo_name", "skill_name"]
                )
            ),
            types.FunctionDeclaration(
                name="modify_skill",
                description="Modify an existing skill's code based on natural language instructions. Use this to fix bugs, add features, or change configuration (e.g. API URLs).",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "skill_name": types.Schema(type=types.Type.STRING, description="The name of the skill to modify"),
                        "instruction": types.Schema(type=types.Type.STRING, description="Instructions for modification"),
                    },
                    required=["skill_name", "instruction"]
                )
            )
        ]

tool_registry = ToolRegistry()
