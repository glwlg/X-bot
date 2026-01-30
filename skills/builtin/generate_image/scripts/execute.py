import base64
import logging
import io
from core.platform.models import UnifiedContext
from telegram import Update
from telegram.ext import ContextTypes
from google.genai import types
from core.config import image_gen_client, IMAGE_MODEL
from utils import smart_reply_text

logger = logging.getLogger(__name__)

async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行文生图任务"""
    logger.info(f"Executing generate_image with params: {params}")
    
    # 兼容常见的参数漂移
    prompt = params.get("prompt") or params.get("instruction") or params.get("query") or ""
    aspect_ratio = params.get("aspect_ratio", "1:1")
    
    if not prompt:
        await ctx.reply("🎨 请描述你想要生成的画面。")
        return "❌ 未提供提示词"
        
    status_msg = await ctx.reply(f"🎨 正在绘图: {prompt} ({aspect_ratio})...")
    
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

        # Call generate_content 
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
                        
                        if part.inline_data:
                            image_bytes = part.inline_data.data
                            break
                        
        if not image_bytes:
             logger.error(f"Image Gen Failed. Full Response Candidates: {response.candidates}")
             msg_id = getattr(status_msg, "message_id", getattr(status_msg, "id", None))
             await ctx.edit_message(msg_id, "❌ 生成失败: API 未返回图片数据 (Candidates Empty or No Inline Data)。")
             return "❌ 生成失败: 无图片数据"

        # 发送图片 - Ensure bytes, use BytesIO to avoid "embedded null byte" path logic in Discord
        if isinstance(image_bytes, bytes):
            image_io = io.BytesIO(image_bytes)
        else:
            # If it's a string (e.g. base64), try to decode or wrap?
            # Usually it's bytes. If it's str, print warning.
            logger.warning(f"Image bytes was {type(image_bytes)}, forcing bytes conversion")
            if isinstance(image_bytes, str):
                 # Try UTF-8? Or Base64? Assuming raw bytes as str
                 image_io = io.BytesIO(image_bytes.encode('utf-8')) # Dangerous assumption
            else:
                 image_io = io.BytesIO(bytes(image_bytes))

        await ctx.reply_photo(
            photo=image_io,
            caption=f"🎨 **Prompt**: {prompt}\n📏 **Ratio**: {aspect_ratio}"
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
        if status_msg:
             try:
                msg_id = getattr(status_msg, "message_id", getattr(status_msg, "id", None))
                await ctx.edit_message(msg_id, f"❌ 绘图失败: {error_msg}")
             except:
                pass
        return f"❌ 绘图失败: {error_msg}"
