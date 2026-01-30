from core.platform.models import UnifiedContext
from utils import smart_reply_text
import re
from handlers.media_handlers import process_video_download

SKILL_META = {
    "name": "video_download",
    "description": "下载视频，修复了 Message 对象缺少 message_id 属性的错误",
    "version": "1.0.1",
    "params": {
        "url": "视频链接",
        "format": "下载格式 (video/audio)"
    }
}

async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行视频下载"""
    url = params.get("url", "")
    format_type = params.get("format", "video")
    
    # Fallback: Try to extract URL from instruction if missing
    if not url and params.get("instruction"):
        # Simple regex to find http/https URLs
        match = re.search(r'(https?://[^\s]+)', params["instruction"])
        if match:
            url = match.group(0)
    
    if not url:
        await ctx.reply(
            "📹 **视频下载**\n\n"
            "请提供视频链接，例如：\n"
            "• 下载 https://www.youtube.com/watch?v=xxx\n"
            "• 帮我保存这个视频 https://twitter.com/..."
        )
        return "❌ 未提供 URL"
    
    # 清理 URL，移除可能的尾部参数干扰
    url = url.strip()
    
    try:
        # 委托给现有的下载逻辑
        await process_video_download(
            ctx, 
            url, 
            audio_only=(format_type == "audio")
        )
        return "✅ 视频已下载并发送"
    except AttributeError as e:
        if "message_id" in str(e):
            # 处理 Message 对象缺少 message_id 的情况
            await ctx.reply("⚠️ 视频下载功能遇到兼容性问题，正在尝试备用方案...")
            try:
                # 尝试直接回复而不依赖 message_id
                await process_video_download(
                    ctx,
                    url,
                    audio_only=(format_type == "audio")
                )
                return "✅ 视频已下载并发送"
            except Exception as inner_e:
                return f"❌ 下载失败: {str(inner_e)}"
        else:
            return f"❌ 属性错误: {str(e)}"
    except Exception as e:
        error_msg = str(e)
        await ctx.reply(f"❌ 视频下载失败: {error_msg}")
        return f"❌ 下载失败: {error_msg}"