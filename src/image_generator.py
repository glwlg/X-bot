"""
AI 画图模块 - 使用 Gemini API 进行提示词优化和图像生成
"""
import logging
import base64
import io
from telegram import Update
from telegram.ext import ContextTypes

from config import gemini_client, GEMINI_MODEL, IMAGE_MODEL

logger = logging.getLogger(__name__)


async def optimize_image_prompt(user_prompt: str) -> str:
    """
    使用 Gemini AI 优化用户的画图提示词
    
    Args:
        user_prompt: 用户原始提示词
        
    Returns:
        优化后的提示词
    """
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"请优化以下画图提示词：{user_prompt}",
            config={
                "system_instruction": (
                    "你是一个专业的 AI 绘画提示词优化专家。"
                    "你的任务是将用户的简单描述转换为详细、专业的英文绘画提示词。"
                    "提示词应该包含：主题、风格、光影、细节、氛围等元素。"
                    "输出格式：直接返回优化后的英文提示词，不要有任何解释或额外文字。"
                ),
            },
        )
        
        optimized_prompt = response.text.strip()
        logger.info(f"Optimized prompt: {optimized_prompt}")
        return optimized_prompt
    
    except Exception as e:
        logger.error(f"Failed to optimize prompt: {e}")
        # 如果优化失败，返回原始提示词
        return user_prompt


async def generate_image(prompt: str) -> bytes | None:
    """
    使用 Gemini Imagen 生成图像
    
    Args:
        prompt: 图像生成提示词
        
    Returns:
        图像字节数据，如果失败则返回 None
    """
    try:
        response = gemini_client.models.generate_images(
            model=IMAGE_MODEL,
            prompt=prompt,
            config={
                "number_of_images": 1,
            },
        )
        
        if response.generated_images:
            image_data = response.generated_images[0].image.image_bytes
            logger.info("Image generated successfully")
            return image_data
        else:
            logger.error("No images generated")
            return None
    
    except Exception as e:
        logger.error(f"Failed to generate image: {e}")
        return None


async def handle_image_generation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_prompt: str
) -> None:
    """
    处理完整的图像生成流程
    
    Args:
        update: Telegram 更新对象
        context: 上下文对象
        user_prompt: 用户提示词
    """
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # 检查用户权限
    from config import is_user_allowed
    if not is_user_allowed(user_id):
        await update.message.reply_text(
            "⛔ 抱歉，您没有使用 AI 画图功能的权限。"
        )
        return
    
    # 发送处理中消息
    status_message = await update.message.reply_text(
        "🎨 正在优化您的提示词...\n\n"
        f"原始提示词：{user_prompt}"
    )
    
    # 步骤1：优化提示词
    optimized_prompt = await optimize_image_prompt(user_prompt)
    
    await status_message.edit_text(
        "🎨 提示词优化完成！\n\n"
        f"原始提示词：{user_prompt}\n\n"
        f"优化后：{optimized_prompt}\n\n"
        "🖼️ 正在生成图像，请稍候..."
    )
    
    # 步骤2：生成图像
    image_data = await generate_image(optimized_prompt)
    
    if image_data:
        # 发送图片
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(image_data),
                caption=f"🎨 <b>AI 生成完成</b>\n\n<b>提示词：</b>{user_prompt}\n\n<b>优化后：</b>{optimized_prompt}",
                parse_mode="HTML",
            )
            # 记录统计
            from stats import increment_stat
            await increment_stat(user_id, "image_generations")
            # 删除状态消息
            await status_message.delete()
        except Exception as e:
            logger.error(f"Failed to send image: {e}")
            await status_message.edit_text(
                "❌ 图片发送失败，请稍后再试。"
            )
    else:
        await status_message.edit_text(
            "❌ 图像生成失败\n\n"
            "可能的原因：\n"
            "• API 配额不足\n"
            "• 提示词违反内容政策\n"
            "• 网络连接问题"
        )
