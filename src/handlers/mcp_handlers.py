"""
MCP 相关 Handler
处理浏览器操作（截图等）请求
"""

import io
import logging
from core.platform.models import UnifiedContext

from core.config import MCP_ENABLED
from utils import smart_reply_text, smart_edit_text

logger = logging.getLogger(__name__)


async def handle_browser_action(
    ctx: UnifiedContext, params: dict
) -> bool:
    """
    处理浏览器操作（截图等）
    
    Args:
    Args:
        ctx: UnifiedContext
        params: 从意图路由提取的参数，包含 url 和 action
        
    Returns:
        True 如果成功处理，False 如果需要回退到普通对话
    """
    if not MCP_ENABLED:
        logger.warning("MCP is disabled, falling back to chat")
        return False
    
    url = params.get("url")
    action = params.get("action", "screenshot")
    
    if not url:
        await ctx.reply("❌ 请提供要操作的网页 URL。\n\n示例：`截图 https://example.com`")
        return True
    
    # 确保 URL 有协议头
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    
    if action == "screenshot":
        return await _handle_screenshot(ctx, url)
    else:
        # 其他 action 可以在这里扩展
        await ctx.reply(f"❌ 暂不支持的操作：`{action}`")
        return True


async def _handle_screenshot(
    ctx: UnifiedContext, url: str
) -> bool:
    """
    处理网页截图请求
    """
    # 发送处理中提示
    thinking_msg = await ctx.reply(
        f"📸 正在截图 `{url}`...\n\n"
        "⏳ 首次使用可能需要较长时间"
    )
    # await ctx.platform_ctx.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
    # UnifiedContext doesn't support chat_action yet, maybe access platform_ctx
    if ctx.platform_ctx:
        try:
             await ctx.platform_ctx.bot.send_chat_action(chat_id=ctx.message.chat.id, action="upload_photo")
        except:
             pass
    
    try:
        # 导入并使用 MCP Manager
        from mcp_client.manager import mcp_manager
        from mcp_client.playwright import register_playwright_server
        
        # 确保 Playwright 服务已注册
        register_playwright_server()
        
        # 步骤0：调整浏览器窗口大小为高分辨率
        logger.info("Resizing browser to 2560x1440...")
        try:
            await mcp_manager.call_tool(
                "playwright",
                "browser_resize",
                {"width": 1920, "height": 1080}
            )
        except Exception as e:
            logger.warning(f"Resize failed (non-critical): {e}")
        
        # 步骤1：先导航到页面并等待加载
        logger.info(f"Navigating to {url}...")
        await mcp_manager.call_tool(
            "playwright",
            "browser_navigate",
            {"url": url}
        )
        
        # 步骤2：等待页面加载完成（使用 browser_wait_for）
        logger.info("Waiting for page to load...")
        try:
            await mcp_manager.call_tool(
                "playwright",
                "browser_wait_for",
                {"time": 2}  # 等待 2 秒
            )
        except Exception as e:
            logger.warning(f"Wait failed (non-critical): {e}")
        
        # 步骤3：截图（fullPage + 高分辨率视口，通过 Docker 参数设置）
        logger.info("Taking fullPage screenshot with high-res viewport...")
        result = await mcp_manager.call_tool(
            "playwright",
            "browser_take_screenshot",
            {"fullPage": False}  # 截取完整页面
        )
        
        # 调试：记录返回的数据结构
        logger.info(f"MCP result type: {type(result)}")
        if isinstance(result, list) and len(result) > 0:
            item = result[0]
            logger.info(f"First item type: {type(item)}, attrs: {dir(item)}")
            if hasattr(item, 'type'):
                logger.info(f"Content type: {item.type}")
            if hasattr(item, 'mimeType'):
                logger.info(f"MIME type: {item.mimeType}")
        
        # 处理返回结果
        screenshot_data = _extract_screenshot_data(result)
        
        if screenshot_data:
            logger.info(f"Screenshot data extracted, size: {len(screenshot_data)} bytes")
            
            # 保存原始截图到本地（调试用）
            import os
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_path = f"/app/downloads/screenshot_{timestamp}.png"
            try:
                with open(debug_path, "wb") as f:
                    f.write(screenshot_data)
                logger.info(f"Debug: saved screenshot to {debug_path}")
            except Exception as e:
                logger.warning(f"Failed to save debug screenshot: {e}")
            
            # 删除 "正在处理" 的消息
            try:
                await thinking_msg.delete()
            except Exception:
                pass
            
            # 发送截图（作为文档发送，避免 Telegram 压缩图片） (Legacy access for reply_document)
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.replace("www.", "")
            filename = f"screenshot_{domain}.png"
            
            screenshot_file = io.BytesIO(screenshot_data)
            screenshot_file.name = filename  # 设置文件名
            
            # Using platform_event/adapter fallback or ctx.reply_document if available?
            # UnifiedContext doesn't have reply_document yet. Use adapter specific via Platform Context.
            
            if ctx.platform_ctx:
                 await ctx.platform_ctx.bot.send_document(
                    chat_id=ctx.message.chat.id,
                    document=screenshot_file,
                    caption=f"📸 网页截图：{url}",
                    parse_mode="Markdown"
                )
            # await update.message.reply_document(
            #     document=screenshot_file,
            #     caption=f"📸 网页截图：{url}",
            #     parse_mode="Markdown"
            # )
            
            # 清理 MCP 连接（释放 Docker 容器）
            await mcp_manager.disconnect_server("playwright")
            
            return True
        else:
            logger.error(f"Failed to extract screenshot data from result: {result}")
            await ctx.edit_message(thinking_msg.message_id, f"❌ 截图失败：无法获取图片数据\n\nURL: `{url}`")
            return True
            
    except Exception as e:
        logger.error(f"Screenshot error: {e}", exc_info=True)
        error_msg = str(e)
        
        # 提供更友好的错误提示
        if "docker" in error_msg.lower():
            error_hint = "Docker 服务不可用，请确保 Docker 已启动"
        elif "timeout" in error_msg.lower():
            error_hint = "操作超时，网页可能加载过慢"
        else:
            error_hint = error_msg[:200]  # 截断过长的错误信息
        
        await ctx.edit_message(
            thinking_msg.message_id,
            f"❌ 截图失败\n\n"
            f"**URL**: `{url}`\n"
            f"**原因**: {error_hint}"
        )
        return True


def _extract_screenshot_data(result) -> bytes | None:
    """
    从 MCP 工具返回结果中提取截图数据
    
    MCP 返回格式为列表，可能包含 TextContent 和 ImageContent
    我们需要找到 ImageContent 并提取其 data 字段
    """
    import base64
    
    if not result:
        return None
    
    # MCP 返回列表格式，遍历所有元素
    if isinstance(result, list):
        for content in result:
            # 检查是否是 ImageContent（type='image'）
            if hasattr(content, 'type') and content.type == 'image':
                if hasattr(content, 'data') and content.data:
                    try:
                        return base64.b64decode(content.data)
                    except Exception as e:
                        logger.error(f"Failed to decode image data: {e}")
                        continue
            
            # 兼容：检查 mimeType 包含 image
            if hasattr(content, 'mimeType') and 'image' in str(content.mimeType):
                if hasattr(content, 'data') and content.data:
                    try:
                        return base64.b64decode(content.data)
                    except Exception:
                        continue
    
    # 情况 2：result 是字典
    if isinstance(result, dict):
        if 'data' in result:
            try:
                return base64.b64decode(result['data'])
            except Exception:
                pass
    
    # 情况 3：result 直接是 bytes
    if isinstance(result, bytes):
        return result
    
    return None
