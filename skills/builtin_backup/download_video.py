"""
视频下载 Skill - 下载视频/音频
"""
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text


SKILL_META = {
    "name": "download_video",
    "description": "下载视频或音频，支持 YouTube, Twitter/X, TikTok 等平台",
    "triggers": ["下载", "download", "save", "保存视频", "视频下载", "get video"],
    "params": {
        "url": {
            "type": "str",
            "description": "视频链接"
        },
        "format": {
            "type": "str",
            "enum": ["video", "audio"],
            "optional": True,
            "description": "下载格式，默认 video"
        }
    },
    "version": "1.0.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> str:
    """执行视频下载"""
    url = params.get("url", "")
    format_type = params.get("format", "video")
    
    # Fallback: Try to extract URL from instruction if missing
    if not url and params.get("instruction"):
        import re
        # Simple regex to find http/https URLs
        match = re.search(r'(https?://[^\s]+)', params["instruction"])
        if match:
            url = match.group(0)
    
    if not url:
        await smart_reply_text(update,
            "📹 **视频下载**\n\n"
            "请提供视频链接，例如：\n"
            "• 下载 https://www.youtube.com/watch?v=xxx\n"
            "• 帮我保存这个视频 https://twitter.com/..."
        )
        return "❌ 未提供 URL"
    
    # 委托给现有的下载逻辑
    from handlers.media_handlers import process_video_download
    
    await process_video_download(
        update, 
        context, 
        url, 
        audio_only=(format_type == "audio")
    )
    return "✅ 视频已下载并发送"

