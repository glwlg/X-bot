"""
MCP 服务抽象基类
定义所有 MCP 服务的通用接口
"""

import logging
from abc import ABC, abstractmethod
from typing import Any
import asyncio

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)


class MCPServerBase(ABC):
    """
    MCP 服务抽象基类

    所有具体的 MCP 服务实现都需要继承此类。
    提供统一的连接、调用、断开接口。

    Change: 使用后台任务维护 stdio_client 会话，
    避免 AsyncExitStack 跨 Task 清理导致 anyio RuntimeError。
    """

    def __init__(self):
        self.session: ClientSession | None = None
        self._serve_task: asyncio.Task | None = None
        self._ready_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._connect_error: Exception | None = None

    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.session is not None and not self._stop_event.is_set()

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
            try:
                response = await self.session.list_tools()
                return [tool.name for tool in response.tools]
            except Exception as e:
                logger.warning(f"[MCP:{self.server_name}] Connection seems stale: {e}")
                # Fall through to reconnect

        # Reset state
        self._stop_event.clear()
        self._ready_event.clear()
        self._connect_error = None

        logger.info(f"[MCP:{self.server_name}] Connecting...")
        self._serve_task = asyncio.create_task(self._run_session_loop())

        # Wait for connection ready or failure
        await self._ready_event.wait()

        if self._connect_error:
            # Clean up
            await self.disconnect()
            raise self._connect_error

        if not self.session:
            raise ConnectionError(
                f"[MCP:{self.server_name}] Failed to establish session (unknown error)"
            )

        try:
            # 列出可用工具
            response = await self.session.list_tools()
            tools = [tool.name for tool in response.tools]
            logger.info(f"[MCP:{self.server_name}] Connected. Available tools: {tools}")
            return tools
        except Exception as e:
            logger.error(
                f"[MCP:{self.server_name}] Failed to list tools after connect: {e}"
            )
            await self.disconnect()
            raise

    async def _run_session_loop(self):
        """后台任务：维持 MCP 会话"""
        try:
            server_params = self.get_server_params()

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    self.session = session
                    await session.initialize()

                    # Signal ready
                    self._ready_event.set()

                    # Keep alive until stop requested
                    await self._stop_event.wait()

        except Exception as e:
            logger.error(f"[MCP:{self.server_name}] Session loop error: {e}")
            self._connect_error = e
        finally:
            self.session = None
            self._ready_event.set()  # Ensure waiters unblock even on crash
            logger.info(f"[MCP:{self.server_name}] Session loop exited")

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """
        调用 MCP 工具
        """
        if not self.is_connected:
            await self.connect()

        logger.info(
            f"[MCP:{self.server_name}] Calling tool: {tool_name} with args: {arguments}"
        )
        try:
            result = await self.session.call_tool(tool_name, arguments)
            logger.debug(f"[MCP:{self.server_name}] Tool result: {result}")
            return result.content
        except Exception as e:
            # Check if connection died
            if not self.is_connected:
                logger.warning(
                    f"[MCP:{self.server_name}] Connection lost during call, retrying..."
                )
                # Note: Simple retry might be dangerous for non-idempotent calls, but useful for generic tools.
                # For now, just raise and let caller decide, or auto-reconnect if implemented.
            raise e

    async def disconnect(self) -> None:
        """断开连接并清理资源"""
        logger.info(f"[MCP:{self.server_name}] Disconnecting...")
        self._stop_event.set()

        if self._serve_task:
            try:
                # Wait for loop to finish cleanup
                # Wrap in timeout to avoid hanging
                import asyncio

                await asyncio.wait_for(asyncio.shield(self._serve_task), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[MCP:{self.server_name}] Disconnect timeout, cancelling task"
                )
                self._serve_task.cancel()
                try:
                    await self._serve_task
                except asyncio.CancelledError:
                    pass
            except Exception as e:
                logger.error(
                    f"[MCP:{self.server_name}] Error waiting for task exit: {e}"
                )
            finally:
                self._serve_task = None
