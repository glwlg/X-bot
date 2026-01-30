from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text
import re
from handlers.media_handlers import process_video_download

SKILL_META = {
    "name": "video_download",
    "description": "下载视频。移除了无法设置 Message.user 属性的 monkey-patch 尝试，改为直接调用下载函数并处理可能的错误。",
    "version": "1.0.5",
    "parameters": {
        "url": {
            "type": "string",
            "description": "视频链接",
            "required": True
        },
        "format": {
            "type": "string",
            "description": "下载格式：video 或 audio",
            "required": False,
            "default": "video"
        }
    }
}

async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> str:
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
        await smart_reply_text(update,
            "📹 **视频下载**\n\n"
            "请提供视频链接，例如：\n"
            "• 下载 https://www.youtube.com/watch?v=xxx\n"
            "• 帮我保存这个视频 https://twitter.com/..."
        )
        return "❌ 未提供 URL"
    
    # 确定是否仅下载音频
    audio_only = (format_type == "audio")
    
    # 将 URL 存储到 context.args 中，因为 process_video_download 期望从 context.args 获取 URL
    context.args = [url]
    
    # 确保 context.user_data 中有用户信息，以防 process_video_download 需要
    # 使用 effective_user 而不是 message.user（后者不存在）
    user = update.effective_user
    if user and hasattr(context, 'user_data'):
        context.user_data['user_id'] = user.id
        context.user_data['user_name'] = user.first_name
    
    try:
        # 委托给现有的下载逻辑
        # process_video_download 只接受 2-3 个参数: update, context, 可选的 audio_only
        await process_video_download(update, context, audio_only)
        return "✅ 视频已下载并发送"
    except Exception as e:
        error_msg = str(e)
        return f"❌ 下载失败: {error_msg}"