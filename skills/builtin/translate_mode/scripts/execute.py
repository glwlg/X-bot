from core.platform.models import UnifiedContext
from repositories import get_user_settings, set_translation_mode
from utils import smart_reply_text

async def execute(ctx: UnifiedContext, params: dict) -> None:
    """执行翻译模式切换"""
    user_id = int(ctx.message.user.id)
    action = params.get("action", "toggle")
    
    settings = await get_user_settings(user_id)
    current_status = settings.get("auto_translate", 0)
    
    if action == "on":
        new_status = True
    elif action == "off":
        new_status = False
    else:  # toggle
        new_status = not current_status
    
    await set_translation_mode(user_id, new_status)
    
    status_text = "🌍 **已开启**" if new_status else "🚫 **已关闭**"
    desc = (
        "现在发送任何文本消息，我都会为您自动翻译。\n(外语->中文，中文->英文)" 
        if new_status else 
        "已恢复正常 AI 助手模式。"
    )
    
    await ctx.reply(
        f"ℹ️ **沉浸式翻译模式**\n\n"
        f"当前状态：{status_text}\n\n"
        f"{desc}"
    )
