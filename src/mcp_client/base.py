"""
MCP 服务抽象基类
定义所有 MCP 服务的通用接口
"""

import logging
from abc import ABC, abstractmethod
from typing import Any
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)


class MCPServerBase(ABC):
    """
    MCP 服务抽象基类
    
    所有具体的 MCP 服务实现都需要继承此类。
    提供统一的连接、调用、断开接口。
    """
    
    def __init__(self):
        self.session: ClientSession | None = None
        self.exit_stack: AsyncExitStack | None = None
        self._connected = False
    
    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self.session is not None
    
    @abstractmethod
    def get_server_params(self) -> StdioServerParameters:
        """
        返回 MCP 服务器的启动参数
        子类必须实现此方法
        """
        pass
    
    @property
    @abstractmethod
    def server_name(self) -> str:
        """服务名称，用于日志和标识"""
        pass
    
    async def connect(self) -> list[str]:
        """
        连接到 MCP 服务
        
        Returns:
            可用工具列表
        """
        if self.is_connected:
            logger.warning(f"[MCP:{self.server_name}] Already connected")
            response = await self.session.list_tools()
            return [tool.name for tool in response.tools]
        
        try:
            logger.info(f"[MCP:{self.server_name}] Connecting...")
            self.exit_stack = AsyncExitStack()
            
            server_params = self.get_server_params()
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            
            self.stdio, self.write = stdio_transport
            self.session = await self.exit_stack.enter_async_context(
                ClientSession(self.stdio, self.write)
            )
            
            await self.session.initialize()
            self._connected = True
            
            # 列出可用工具
            response = await self.session.list_tools()
            tools = [tool.name for tool in response.tools]
            logger.info(f"[MCP:{self.server_name}] Connected. Available tools: {tools}")
            return tools
            
        except Exception as e:
            logger.error(f"[MCP:{self.server_name}] Connection failed: {e}")
            await self.disconnect()
            raise
    
    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """
        调用 MCP 工具
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            工具返回结果
        """
        if not self.is_connected:
            await self.connect()
        
        logger.info(f"[MCP:{self.server_name}] Calling tool: {tool_name} with args: {arguments}")
        result = await self.session.call_tool(tool_name, arguments)
        logger.debug(f"[MCP:{self.server_name}] Tool result: {result}")
        return result.content
    
    async def disconnect(self) -> None:
        """断开连接并清理资源"""
        if self.exit_stack:
            try:
                await self.exit_stack.aclose()
                logger.info(f"[MCP:{self.server_name}] Disconnected")
            except Exception as e:
                logger.error(f"[MCP:{self.server_name}] Error during disconnect: {e}")
            finally:
                self.exit_stack = None
                self.session = None
                self._connected = False
