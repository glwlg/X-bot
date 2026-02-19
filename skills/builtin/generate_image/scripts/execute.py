import asyncio
import logging
import base64
import time
from core.platform.models import UnifiedContext
from core.config import IMAGE_MODEL, openai_client

logger = logging.getLogger(__name__)


def _aspect_ratio_to_size(aspect_ratio: str) -> str:
    ratio = str(aspect_ratio or "").strip()
    mapping = {
        "1:1": "1024x1024",
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "4:3": "1536x1024",
        "3:4": "1024x1536",
    }
    return mapping.get(ratio, "1024x1024")


def _resolve_prompt(ctx: UnifiedContext, params: dict) -> str:
    candidates = [
        params.get("prompt"),
        params.get("instruction"),
        params.get("query"),
        params.get("text"),
    ]
    message = getattr(ctx, "message", None)
    candidates.append(getattr(message, "text", ""))

    for value in candidates:
        prompt = str(value or "").strip()
        if prompt:
            return prompt
    return ""


def _build_caption(prompt: str) -> str:
    text = " ".join(str(prompt or "").strip().split())
    if len(text) > 48:
        text = text[:48].rstrip() + "..."
    return f"配文：{text}" if text else ""


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> dict[str, object]:
    """执行文生图任务"""
    logger.info(f"Executing generate_image with params: {params}")

    # 兼容常见的参数漂移
    prompt = _resolve_prompt(ctx, params)
    aspect_ratio = params.get("aspect_ratio", "1:1")

    if not prompt:
        return {
            "success": False,
            "failure_mode": "recoverable",
            "text": "❌ 缺少绘图提示词，请提供想生成的画面描述。",
        }

    # status_msg = None

    try:
        if openai_client is None:
            return {
                "success": False,
                "failure_mode": "fatal",
                "text": "❌ 绘图失败: OpenAI 客户端未初始化。",
            }

        size = _aspect_ratio_to_size(aspect_ratio)
        timeout_seconds = 180
        response = await asyncio.wait_for(
            asyncio.to_thread(
                openai_client.images.generate,
                model=IMAGE_MODEL,
                prompt=prompt,
                size=size,
                response_format="b64_json",
            ),
            timeout=timeout_seconds,
        )

        image_bytes = b""
        data = getattr(response, "data", None) or []
        if data:
            first = data[0]
            b64_payload = str(getattr(first, "b64_json", "") or "")
            if b64_payload:
                image_bytes = base64.b64decode(b64_payload)

        if not image_bytes:
            return {
                "success": False,
                "failure_mode": "recoverable",
                "text": "❌ 生成失败: API 未返回图片数据。",
            }

        safe_prompt = "".join([c for c in prompt if c.isalnum()])[:20]
        filename = f"gen_{safe_prompt}_{int(time.time())}.png"
        caption = _build_caption(prompt)
        text_parts = [
            "✅ 图片已生成。",
            f"📏 比例: {aspect_ratio}",
        ]
        if caption:
            text_parts.append(caption)

        return {
            "text": "\n".join(text_parts),
            "files": {filename: image_bytes},
            "task_outcome": "done",
            "terminal": True,
        }

    except asyncio.TimeoutError:
        logger.error("Image generation timed out after 180 seconds")
        return {
            "success": False,
            "failure_mode": "recoverable",
            "text": "❌ 绘图超时（180 秒），请稍后重试或简化提示词。",
        }
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        error_msg = str(e)
        return {
            "success": False,
            "failure_mode": "recoverable",
            "text": f"❌ 绘图失败: {error_msg}",
        }
