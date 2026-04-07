from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from core.config import get_client_for_model
from core.model_config import (
    get_model_id_for_api,
    resolve_models_config_path,
    select_model_for_role,
)
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)

_ASPECT_RATIO_TO_SIZE = {
    "1:1": "2048x2048",
    "16:9": "3584x2048",
    "9:16": "2048x3584",
    "4:3": "1536x1024",
    "3:4": "1024x1536",
}


def _normalize_aspect_ratio(value: Any) -> str:
    token = str(value or "").strip()
    if token in _ASPECT_RATIO_TO_SIZE:
        return token
    return "1:1"


def _resolve_prompt(ctx: UnifiedContext, params: dict[str, Any]) -> str:
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
    normalized = " ".join(str(prompt or "").strip().split())
    if len(normalized) > 120:
        normalized = normalized[:120].rstrip() + "..."
    return f"配文：{normalized}" if normalized else ""


def _safe_filename(prompt: str) -> str:
    safe = "".join(ch for ch in str(prompt or "") if ch.isalnum())[:20]
    return safe or "image"


def _should_fallback_to_chat_completions(detail: str) -> bool:
    lowered = str(detail or "").strip().lower()
    return (
        "404" in lowered
        or "page not found" in lowered
        or "images.generate" in lowered
    )


def _extract_image_bytes(response: Any) -> bytes:
    data = getattr(response, "data", None) or []
    for item in data:
        payload = str(getattr(item, "b64_json", "") or "")
        if payload:
            return base64.b64decode(payload)

    choices = getattr(response, "choices", None) or []
    for choice in choices:
        message = getattr(choice, "message", None)
        images = getattr(message, "images", None) or []
        for image in images:
            if isinstance(image, dict):
                payload = str(image.get("b64_json", "") or "")
                image_url = image.get("image_url")
            else:
                payload = str(getattr(image, "b64_json", "") or "")
                image_url = getattr(image, "image_url", None)
            if payload:
                return base64.b64decode(payload)
            if isinstance(image_url, dict):
                url = str(image_url.get("url", "") or "")
            else:
                url = str(getattr(image_url, "url", "") or "")
            if not url.startswith("data:image/") or "," not in url:
                continue
            _, encoded = url.split(",", 1)
            if encoded:
                return base64.b64decode(encoded)
    return b""


async def _generate_via_images_api(
    client: Any,
    *,
    model_key: str,
    prompt: str,
    aspect_ratio: str,
) -> Any:
    return await asyncio.wait_for(
        asyncio.to_thread(
            client.images.generate,
            model=get_model_id_for_api(model_key),
            prompt=prompt,
            size=_ASPECT_RATIO_TO_SIZE[aspect_ratio],
            response_format="b64_json",
            extra_body={
                "watermark": False,  # 如果代理支持，可以关闭水印以获得纯净的图片数据
            }
        ),
        timeout=180,
    )


async def _generate_via_chat_completions(
    client: Any,
    *,
    model_key: str,
    prompt: str,
    aspect_ratio: str,
) -> Any:
    fallback_prompt = (
        f"{prompt}\n\n请直接生成图片，不要只返回文字。"
        f" 目标比例：{aspect_ratio}。"
    )
    return await asyncio.wait_for(
        asyncio.to_thread(
            client.chat.completions.create,
            model=get_model_id_for_api(model_key),
            messages=[{"role": "user", "content": fallback_prompt}],
        ),
        timeout=180,
    )


async def execute(
    ctx: UnifiedContext,
    params: dict[str, Any],
    runtime=None,
) -> dict[str, Any]:
    _ = runtime
    prompt = _resolve_prompt(ctx, params)
    if not prompt:
        return {
            "success": False,
            "failure_mode": "recoverable",
            "text": "❌ 缺少绘图提示词，请提供想生成的画面描述。",
        }

    aspect_ratio = _normalize_aspect_ratio(params.get("aspect_ratio"))
    model_key = select_model_for_role("image_generation")
    if not model_key:
        return {
            "success": False,
            "failure_mode": "fatal",
            "text": (
                f"❌ 当前没有可用的生图模型，可能是未配置，或已达到当日图片额度。"
                f" 请在 {resolve_models_config_path()} 中设置 `model.image_generation`。"
            ),
        }
    client = get_client_for_model(model_key, is_async=False)
    if client is None:
        return {
            "success": False,
            "failure_mode": "fatal",
            "text": "❌ 当前没有可用的图像模型或 API Key，无法执行生图。",
        }

    try:
        response = await _generate_via_images_api(
            client,
            model_key=model_key,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    except asyncio.TimeoutError:
        logger.error("generate_image timed out after 180 seconds")
        return {
            "success": False,
            "failure_mode": "recoverable",
            "text": "❌ 生图超时（180 秒），请稍后重试或简化提示词。",
        }
    except Exception as exc:
        detail = str(exc or "").strip()
        logger.warning("generate_image images API failed: %s", detail or exc)
        if _should_fallback_to_chat_completions(detail):
            try:
                response = await _generate_via_chat_completions(
                    client,
                    model_key=model_key,
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                )
            except asyncio.TimeoutError:
                logger.error("generate_image chat fallback timed out after 180 seconds")
                return {
                    "success": False,
                    "failure_mode": "recoverable",
                    "text": "❌ 生图超时（180 秒），请稍后重试或简化提示词。",
                }
            except Exception as fallback_exc:
                fallback_detail = str(fallback_exc or "").strip() or detail or "unknown error"
                logger.error(
                    "generate_image chat fallback failed: %s",
                    fallback_detail,
                )
                return {
                    "success": False,
                    "failure_mode": "fatal",
                    "text": (
                        "❌ 当前生图模型对应的接口不支持生图（"
                        f"{fallback_detail}"
                        "）。请检查代理是否实现了 OpenAI `images.generate`"
                        " 或 `chat.completions` 图片输出兼容。"
                    ),
                }
        else:
            logger.error("generate_image failed: %s", detail or exc)
            return {
                "success": False,
                "failure_mode": "recoverable",
                "text": f"❌ 生图失败: {detail or exc}",
            }

    image_bytes = _extract_image_bytes(response)

    if not image_bytes:
        return {
            "success": False,
            "failure_mode": "recoverable",
            "text": "❌ 生图失败：模型没有返回图片数据。",
        }

    filename = f"gen_{_safe_filename(prompt)}_{int(time.time())}.png"
    caption = _build_caption(prompt)
    lines = [
        "✅ 图片已生成。",
        f"📏 比例: {aspect_ratio}",
    ]
    if caption:
        lines.append(caption)

    return {
        "ok": True,
        "text": "\n".join(lines),
        "files": {filename: image_bytes},
        "task_outcome": "done",
        "terminal": True,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an image from a text prompt.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text. If omitted, --message-text or ctx.message.text is used.",
    )
    parser.add_argument(
        "--aspect-ratio",
        default="1:1",
        choices=sorted(_ASPECT_RATIO_TO_SIZE.keys()),
        help="Image aspect ratio. Defaults to 1:1.",
    )
    add_common_arguments(parser)
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    prompt = " ".join(str(item or "").strip() for item in list(args.prompt or [])).strip()
    explicit = {
        "prompt": prompt or None,
        "aspect_ratio": _normalize_aspect_ratio(getattr(args, "aspect_ratio", "1:1")),
    }
    return merge_params(args, explicit)


async def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
