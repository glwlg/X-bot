"""
图片生成 Skill - AI 生成图片
"""
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text


SKILL_META = {
    "name": "generate_image",
    "description": "使用 AI 生成图片，支持各种描述",
    "triggers": ["画", "draw", "生成图片", "generate image", "绘图", "图片"],
    "params": {
        "prompt": {
            "type": "str",
            "description": "图片描述"
        }
    },
    "version": "1.0.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    """执行图片生成"""
    prompt = params.get("prompt", "")
    
    if not prompt:
        await smart_reply_text(update,
            "🎨 **AI 画图**\n\n"
            "请描述您想要生成的图片，例如：\n"
            "• 画一只可爱的猫咪\n"
            "• 生成一张日落风景图"
        )
        return
    
    # 委托给现有的图片生成逻辑
    from handlers.media_handlers import process_image_generation
    
    await process_image_generation(update, context, prompt)
