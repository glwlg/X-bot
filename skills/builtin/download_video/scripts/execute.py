from core.platform.models import UnifiedContext
import re
from handlers.media_handlers import process_video_download


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行视频下载"""
    url = params.get("url", "")
    format_type = params.get("format", "video")

    # Fallback: Try to extract URL from instruction if missing
    if not url and params.get("instruction"):
        # Simple regex to find http/https URLs
        match = re.search(r"(https?://[^\s]+)", params["instruction"])
        if match:
            url = match.group(0)

    if not url:
        return {
            "text": (
                "📹 **视频下载**\n\n"
                "请提供视频链接，例如：\n"
                "• 下载 https://www.youtube.com/watch?v=xxx\n"
                "• 帮我保存这个视频 https://twitter.com/..."
            ),
            "ui": {},
        }

    await process_video_download(ctx, url, audio_only=(format_type == "audio"))
    await process_video_download(ctx, url, audio_only=(format_type == "audio"))
    return {"text": "✅ 视频已下载并发送", "ui": {}}
