"""
MCP (Model Context Protocol) 模块
提供与外部 MCP 服务交互的能力
"""

from mcp_client.manager import MCPManager, mcp_manager
from mcp_client.base import MCPServerBase

__all__ = ["MCPManager", "mcp_manager", "MCPServerBase"]
