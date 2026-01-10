"""
Playwright MCP 服务实现
通过 Docker 运行 Microsoft Playwright MCP 服务
"""

import logging
import base64
import os
from typing import Any

from mcp.client.stdio import StdioServerParameters

from mcp_client.base import MCPServerBase

logger = logging.getLogger(__name__)

# 从环境变量或使用默认值
PLAYWRIGHT_MCP_IMAGE = os.getenv("MCP_PLAYWRIGHT_IMAGE", "mcr.microsoft.com/playwright/mcp")


class PlaywrightMCPServer(MCPServerBase):
    """
    Playwright 浏览器自动化 MCP 服务
    
    通过 Docker 启动 Microsoft 官方 Playwright MCP 镜像，
    提供浏览器截图、导航、点击等功能。
    """
    
    @property
    def server_name(self) -> str:
        return "playwright"
    
    def get_server_params(self) -> StdioServerParameters:
        """返回 Docker 启动参数（覆盖 ENTRYPOINT 添加 --viewport-size）"""
        return StdioServerParameters(
            command="docker",
            args=[
                "run",
                "-i",
                "--rm",
                "--init",
                "--entrypoint", "node",  # 覆盖默认 ENTRYPOINT
                PLAYWRIGHT_MCP_IMAGE,
                # 手动指定完整命令（原 ENTRYPOINT 内容 + viewport-size）
                "cli.js",
                "--headless",
                "--browser", "chromium",
                "--no-sandbox",
                "--viewport-size", "1920x1080"
            ],
            env=None
        )
    
    async def screenshot(self, url: str, full_page: bool = False) -> bytes:
        """
        便捷方法：截取网页截图
        
        Args:
            url: 要截图的 URL
            full_page: 是否截取完整页面
            
        Returns:
            PNG 图片数据（bytes）
        """
        result = await self.call_tool("browser_take_screenshot", {
            "url": url,
            "fullPage": full_page
        })
        
        # 解析返回的图片数据
        # MCP 返回格式可能是 base64 编码的图片
        if isinstance(result, list) and len(result) > 0:
            content = result[0]
            if hasattr(content, 'data'):
                # 如果是 base64 编码
                return base64.b64decode(content.data)
            elif hasattr(content, 'text'):
                # 如果是 base64 字符串
                return base64.b64decode(content.text)
        
        # 直接返回原始数据
        return result
    
    async def navigate(self, url: str) -> str:
        """
        便捷方法：导航到指定 URL
        
        Args:
            url: 目标 URL
            
        Returns:
            页面标题或状态
        """
        result = await self.call_tool("browser_navigate", {"url": url})
        if isinstance(result, list) and len(result) > 0:
            return result[0].text if hasattr(result[0], 'text') else str(result[0])
        return str(result)
    
    async def get_page_content(self) -> str:
        """
        便捷方法：获取当前页面的文本内容
        
        Returns:
            页面文本内容
        """
        result = await self.call_tool("browser_snapshot", {})
        if isinstance(result, list) and len(result) > 0:
            return result[0].text if hasattr(result[0], 'text') else str(result[0])
        return str(result)


def register_playwright_server():
    """注册 Playwright MCP 服务到管理器"""
    from mcp_client.manager import mcp_manager
    mcp_manager.register_server_class("playwright", PlaywrightMCPServer)
    logger.info("[MCP] Playwright server class registered")
