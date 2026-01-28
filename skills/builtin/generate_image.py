"""
文生图 Skill - 使用 Gemini Imagen 生成图片
"""
import base64
import logging
from telegram import Update
from telegram.ext import ContextTypes
from google.genai import types
from core.config import image_gen_client, IMAGE_MODEL
from utils import smart_reply_text

logger = logging.getLogger(__name__)

SKILL_META = {
    "name": "generate_image",
    "description": "使用 AI 生成图片 (Imagen 3)",
    "triggers": ["画图", "生成图片", "绘图", "image", "paint", "draw", "imagine"],
    "params": {
        "prompt": {
            "type": "str",
            "description": "画面描述 (提示词)",
            "required": True
        },
        "aspect_ratio": {
            "type": "str",
            "description": "长宽比，可选: 1:1, 16:9, 9:16, 4:3, 3:4",
            "default": "1:1"
        }
    },
    "version": "1.1.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> str:
    """执行文生图任务"""
    logger.info(f"Executing generate_image with params: {params}")
    
    # 兼容常见的参数漂移
    prompt = params.get("prompt") or params.get("instruction") or params.get("query") or ""
    aspect_ratio = params.get("aspect_ratio", "1:1")
    
    if not prompt:
        await smart_reply_text(update, "🎨 请描述你想要生成的画面。")
        return "❌ 未提供提示词"
        
    status_msg = await smart_reply_text(update, f"🎨 正在绘图: {prompt} ({aspect_ratio})...")
    
    try:
        # Construct content object
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt)
                ]
            )
        ]

        # Config exactly as per user example (with dynamic Aspect Ratio)
        generate_content_config = types.GenerateContentConfig(
            temperature=1,
            top_p=0.95,
            max_output_tokens=8192,
            response_modalities=["IMAGE"], # Request Image only for this skill
            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
            ],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size="1K", # Or whatever default
                output_mime_type="image/png",
            ),
        )

        # Call generate_content (streaming supported but we might just wait for full response)
        # Using non-stream for simplicity in image extraction first
        response = image_gen_client.models.generate_content(
            model=IMAGE_MODEL,
            contents=contents,
            config=generate_content_config,
        )
        
        # DEBUG LOGGING
        logger.info(f"Image API Response Type: {type(response)}")
        
        image_bytes = None
        
        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        # Check for inline_data (image)
                        if part.inline_data:
                            image_bytes = part.inline_data.data
                            break
                        # Future: handle function_call or text if multi-modal
                        
                        if part.inline_data:
                            image_bytes = part.inline_data.data
                            break
                        
        if not image_bytes:
             logger.error(f"Image Gen Failed. Full Response Candidates: {response.candidates}")
             await status_msg.edit_text("❌ 生成失败: API 未返回图片数据 (Candidates Empty or No Inline Data)。")
             return "❌ 生成失败: 无图片数据"

        # 发送图片
        await update.message.reply_photo(
            photo=image_bytes,
            caption=f"🎨 **Prompt**: {prompt}\n📏 **Ratio**: {aspect_ratio}",
            parse_mode="Markdown"
        )
        
        # 删除进度消息
        try:
            await status_msg.delete()
        except:
            pass
            
        return "✅ 图片生成并发送成功"
        
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        error_msg = str(e)
        await status_msg.edit_text(f"❌ 绘图失败: {error_msg}")
        return f"❌ 绘图失败: {error_msg}"
