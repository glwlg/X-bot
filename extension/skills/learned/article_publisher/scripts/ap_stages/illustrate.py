"""Illustrate stage – generates cover and section images for an article."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from core.platform.models import UnifiedContext

from ap_utils import augment_image_prompt, derive_topic_requirements, topic_slug
from ap_stages import StageResult

logger = logging.getLogger(__name__)


async def illustrate_stage(
    ctx: UnifiedContext,
    *,
    topic: str,
    source_path: str | None = None,
    article_data: dict[str, Any] | None = None,
    author: str = "Ikaros",
    output_dir: str,
) -> StageResult:
    """Run the illustrate stage.

    Accepts EITHER:
    - ``article_data`` dict (from write stage), OR
    - ``source_path`` pointing to an ``article.json`` file.

    Returns a ``StageResult`` with image bytes in *files* and
    image path mappings in *data["images"]*.
    """
    # -- resolve source --------------------------------------------------------
    if source_path and not article_data:
        src = Path(source_path)
        if not src.exists():
            return StageResult.fail(f"Source file not found: {source_path}")
        try:
            article_data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as exc:
            return StageResult.fail(f"Invalid article JSON: {exc}")

    if not article_data:
        return StageResult.fail("无文章数据输入")

    if not topic:
        topic = str(article_data.get("title") or "untitled")

    # -- generate images -------------------------------------------------------
    cover_bytes, section_images, generated_files = await _generate_images(
        ctx, article_data, author=author,
    )

    # -- validate: at least cover must succeed ---------------------------------
    if not cover_bytes:
        return StageResult.fail("封面图生成失败，可重试")

    # -- save images to disk ---------------------------------------------------
    effective_topic = str(derive_topic_requirements(topic)["subject"] or topic).strip() or topic
    slug = topic_slug(effective_topic)
    images_dir = Path(output_dir) / slug / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    image_paths: dict[str, str] = {}
    if cover_bytes:
        cover_path = images_dir / "cover.png"
        cover_path.write_bytes(cover_bytes)
        image_paths["cover"] = str(cover_path)

    for idx, img_bytes in section_images.items():
        img_path = images_dir / f"section_{idx}.png"
        img_path.write_bytes(img_bytes)
        image_paths[f"section_{idx}"] = str(img_path)

    # -- build output article_with_images --------------------------------------
    output_data = dict(article_data)
    output_data["images"] = image_paths

    out_dir = Path(output_dir) / slug
    out_path = out_dir / "article_with_images.json"
    out_path.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return StageResult.success(
        output_data,
        output_path=str(out_path),
        files=generated_files,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _generate_images(
    ctx: UnifiedContext,
    article_data: dict[str, Any],
    *,
    author: str,
) -> tuple[bytes | None, dict[int, bytes], dict[str, bytes]]:
    image_tasks: list[tuple[str, int, str]] = []

    if article_data.get("cover_prompt"):
        image_tasks.append(("cover", -1, str(article_data["cover_prompt"])))

    sections = list(article_data.get("sections") or [])
    for idx, sec in enumerate(sections):
        prompt = sec.get("image_prompt")
        if prompt:
            image_tasks.append(("section", idx, str(prompt)))

    if not image_tasks:
        return None, {}, {}

    async def gen_img(task: tuple[str, int, str]) -> tuple[str, int, bytes | None]:
        image_type, image_idx, prompt = task
        try:
            full_prompt = augment_image_prompt(prompt, author)
            result = await ctx.run_skill(
                "generate_image",
                {"prompt": full_prompt, "aspect_ratio": "16:9"},
            )
            if isinstance(result, dict) and isinstance(result.get("files"), dict):
                files = list(result["files"].values())
                if files:
                    return image_type, image_idx, files[0]
        except Exception as exc:
            logger.warning(
                "Image generation failed for %s:%s: %s",
                image_type,
                image_idx,
                exc,
            )
        return image_type, image_idx, None

    img_results = await asyncio.gather(*(gen_img(task) for task in image_tasks))

    cover_bytes: bytes | None = None
    section_images: dict[int, bytes] = {}
    generated_files: dict[str, bytes] = {}

    for result_type, result_idx, result_bytes in img_results:
        if not result_bytes:
            continue

        file_name = f"img_{result_type}_{result_idx}.png"
        generated_files[file_name] = result_bytes

        if result_type == "cover":
            cover_bytes = result_bytes
        else:
            section_images[result_idx] = result_bytes

    return cover_bytes, section_images, generated_files
