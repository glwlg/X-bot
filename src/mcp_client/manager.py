"""
MCP 服务管理器
负责管理多个 MCP 服务的生命周期
"""

import logging
from typing import Any

from mcp_client.base import MCPServerBase

logger = logging.getLogger(__name__)


class MCPManager:
    """
    MCP 服务管理器
    
    管理多个 MCP 服务的生命周期，提供统一的访问接口。
    采用单例模式，全局共享一个实例。
    """
    
    def __init__(self):
        self._servers: dict[str, MCPServerBase] = {}
        self._server_classes: dict[str, type[MCPServerBase]] = {}
    
    def register_server_class(self, server_type: str, server_class: type[MCPServerBase]) -> None:
        """
        注册 MCP 服务类
        
        Args:
            server_type: 服务类型标识（如 "playwright"）
            server_class: 服务类（继承自 MCPServerBase）
        """
        self._server_classes[server_type] = server_class
        logger.info(f"[MCPManager] Registered server class: {server_type}")
    
    async def get_server(self, server_type: str, **kwargs) -> MCPServerBase:
        """
        获取指定类型的 MCP 服务实例
        
        如果服务尚未创建，会自动创建并连接。
        对于像 memory 这样的服务，可以传递 user_id 参数来获取特定的实例。
        例如: get_server("memory", user_id=123) 会创建/获取 'memory_123' 实例
        
        Args:
            server_type: 服务类型标识
            **kwargs: 传递给服务构造函数的参数，也会用于生成唯一的实例 key
            
        Returns:
            MCPServerBase 实例
        """
        # 生成唯一的实例 key
        instance_key = server_type
        if "user_id" in kwargs:
             instance_key = f"{server_type}_{kwargs['user_id']}"
        
        if instance_key in self._servers:
            server = self._servers[instance_key]
            if server.is_connected:
                return server
            # 如果断开了，尝试重新连接
            await server.connect()
            return server
        
        if server_type not in self._server_classes:
            raise ValueError(f"Unknown MCP server type: {server_type}")
        
        # 创建新实例
        server_class = self._server_classes[server_type]
        server = server_class(**kwargs) # 传递参数给构造函数
        await server.connect()
        self._servers[instance_key] = server
        return server
    
    async def call_tool(self, server_type: str, tool_name: str, arguments: dict) -> Any:
        """
        便捷方法：直接调用指定服务的工具
        
        Args:
            server_type: 服务类型标识
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            工具返回结果
        """
        server = await self.get_server(server_type)
        return await server.call_tool(tool_name, arguments)
    
    async def disconnect_server(self, server_type: str) -> None:
        """断开指定服务的连接"""
        if server_type in self._servers:
            await self._servers[server_type].disconnect()
            del self._servers[server_type]
            logger.info(f"[MCPManager] Disconnected server: {server_type}")
    
    async def cleanup_all(self) -> None:
        """清理所有活跃的 MCP 服务"""
        logger.info(f"[MCPManager] Cleaning up {len(self._servers)} servers...")
        for server_type in list(self._servers.keys()):
            await self.disconnect_server(server_type)
        logger.info("[MCPManager] All servers cleaned up")
    
    @property
    def active_servers(self) -> list[str]:
        """返回当前活跃的服务列表"""
        return list(self._servers.keys())


# 全局单例
mcp_manager = MCPManager()
