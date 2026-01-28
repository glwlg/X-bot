"""
视频下载 Skill - 下载视频/音频
"""
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text


SKILL_META = {
    "name": "download_video",
    "description": "下载视频或音频，支持 YouTube, Twitter/X, TikTok 等平台。修复了 is_audio 参数传递错误的问题。",
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
    "version": "1.0.1",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
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
        return
    
    # 委托给现有的下载逻辑
    from handlers.media_handlers import process_video_download
    
    # 根据 process_video_download 的实际签名调用
    # 不传递 is_audio 参数，改为直接传递 url 和 context
    if format_type == "audio":
        # 如果需要音频格式，在 context.user_data 中设置标记
        context.user_data["download_audio_only"] = True
    else:
        context.user_data["download_audio_only"] = False
    
    await process_video_download(update, context, url)