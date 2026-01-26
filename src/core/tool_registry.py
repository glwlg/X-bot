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
        tools.append(self._get_discovery_tool())
        
        return tools

    def _get_native_tools(self) -> List[types.FunctionDeclaration]:
        """Define native system capabilities"""
        return [
            types.FunctionDeclaration(
                name="download_video",
                description="Download a video or audio from a URL (YouTube, Twitter/X, TikTok, Instagram, Bilibili, etc).",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "url": types.Schema(type=types.Type.STRING, description="The video URL"),
                        "audio_only": types.Schema(type=types.Type.BOOLEAN, description="Whether to extract audio only (default false)"),
                    },
                    required=["url"]
                )
            ),
            types.FunctionDeclaration(
                name="set_reminder",
                description="Set a timer or reminder.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "time_expression": types.Schema(type=types.Type.STRING, description="Time expression (e.g., '10m', '1h', 'tomorrow 9am')"),
                        "content": types.Schema(type=types.Type.STRING, description="What to remind about"),
                    },
                    required=["time_expression", "content"]
                )
            ),
            types.FunctionDeclaration(
                name="rss_subscribe",
                description="Subscribe to an RSS feed URL.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "url": types.Schema(type=types.Type.STRING, description="The RSS feed URL"),
                    },
                    required=["url"]
                )
            ),
            types.FunctionDeclaration(
                name="monitor_keyword",
                description="Monitor news for a specific keyword.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "keyword": types.Schema(type=types.Type.STRING, description="The keyword to monitor"),
                    },
                    required=["keyword"]
                )
            ),
            types.FunctionDeclaration(
                name="stock_watch",
                description="Add or remove stock from watchlist.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "stock_name": types.Schema(type=types.Type.STRING, description="Stock name or code"),
                        "action": types.Schema(type=types.Type.STRING, description="Action: 'add' or 'remove'"),
                    },
                    required=["stock_name"]
                )
            ),
            types.FunctionDeclaration(
                name="list_subscriptions",
                description="List current subscriptions (RSS feeds, Stocks, etc).",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "type": types.Schema(type=types.Type.STRING, description="Type to list: 'rss', 'stock', 'all'. Default is 'all'."),
                    },
                    required=["type"]
                )
            ),
        ]

    def _get_skill_tool(self) -> types.FunctionDeclaration:
        """
        Unified tool to call installed skills.
        Dynamically builds description based on available skills.
        """
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

    def _get_discovery_tool(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name="search_and_install_skill",
            description="Search and install a new skill/tool from the marketplace when the user asks for a capability NOT currently supported.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Search query for the needed functionality"),
                },
                required=["query"]
            )
        )

tool_registry = ToolRegistry()
