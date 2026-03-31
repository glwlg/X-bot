"""Publish stage – publishes an illustrated article to one or more channels."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.platform.models import UnifiedContext

from ap_utils import topic_slug
from ap_utils.wechat import (
    prepare_wechat_publisher,
    publish_to_wechat,
)
from ap_utils.xiaohongshu import (
    build_xiaohongshu_note_attachment,
    fallback_xiaohongshu_note,
    generate_xiaohongshu_note_json,
    prepare_xiaohongshu_opencli,
    publish_to_xiaohongshu,
)
from ap_stages import StageResult

logger = logging.getLogger(__name__)


async def publish_stage(
    ctx: UnifiedContext,
    *,
    topic: str,
    source_path: str | None = None,
    article_data: dict[str, Any] | None = None,
    cover_bytes: bytes | None = None,
    section_images: dict[int, bytes] | None = None,
    channels: list[str],
    accounts: dict[str, dict[str, Any] | None],
    output_dir: str,
) -> StageResult:
    """Run the publish stage.

    Publishes to the requested *channels* (``wechat``, ``xiaohongshu``).
    When *source_path* is given, loads article_with_images.json and
    reconstructs image bytes from referenced file paths.
    """
    if section_images is None:
        section_images = {}

    # -- resolve source --------------------------------------------------------
    if source_path and not article_data:
        src = Path(source_path)
        if not src.exists():
            return StageResult.fail(f"Source file not found: {source_path}")
        try:
            article_data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as exc:
            return StageResult.fail(f"Invalid article JSON: {exc}")

        # reconstruct image bytes from disk paths
        images_map = article_data.get("images") or {}
        if isinstance(images_map, dict):
            cover_path = images_map.get("cover")
            if cover_path and Path(cover_path).exists():
                cover_bytes = Path(cover_path).read_bytes()
            for key, img_path in images_map.items():
                if key.startswith("section_") and Path(img_path).exists():
                    idx = int(key.removeprefix("section_"))
                    section_images[idx] = Path(img_path).read_bytes()

    if not article_data:
        return StageResult.fail("无文章数据输入")

    if not topic:
        topic = str(article_data.get("title") or "untitled")

    if not channels:
        return StageResult.fail("未指定发布渠道")

    # -- publish ---------------------------------------------------------------
    publish_statuses: list[str] = []
    generated_files: dict[str, bytes | str] = {}
    has_fatal = False

    # WeChat
    if "wechat" in channels:
        wechat_account = accounts.get("wechat")
        publisher, preflight_error = await prepare_wechat_publisher(wechat_account)
        if preflight_error:
            publish_statuses.append(preflight_error)
            has_fatal = True
        else:
            try:
                status = await publish_to_wechat(
                    publisher=publisher,
                    article_data=article_data,
                    cover_bytes=cover_bytes,
                    section_images=section_images,
                )
                publish_statuses.append(status)
            except Exception as exc:
                logger.error("WeChat publish failed: %s", exc, exc_info=True)
                publish_statuses.append(f"❌ 微信发布失败: {exc}")

    # Xiaohongshu
    if "xiaohongshu" in channels:
        preflight_error = await prepare_xiaohongshu_opencli()
        if preflight_error:
            publish_statuses.append(preflight_error)
            has_fatal = True
        else:
            # generate note
            try:
                note_data = await generate_xiaohongshu_note_json(topic, article_data)
            except Exception as exc:
                logger.warning("Xiaohongshu note generation failed, using fallback: %s", exc)
                note_data = fallback_xiaohongshu_note(topic, article_data)

            generated_files["xiaohongshu_note.txt"] = build_xiaohongshu_note_attachment(note_data)
            generated_files["xiaohongshu_note.json"] = json.dumps(
                note_data, ensure_ascii=False, indent=2,
            ).encode("utf-8")

            try:
                status = await publish_to_xiaohongshu(
                    topic=topic,
                    note_data=note_data,
                    cover_bytes=cover_bytes,
                    section_images=section_images,
                )
                publish_statuses.append(status)
            except Exception as exc:
                logger.error("Xiaohongshu publish failed: %s", exc, exc_info=True)
                publish_statuses.append(f"❌ 小红书发布失败: {exc}")

    # -- save result -----------------------------------------------------------
    slug = topic_slug(topic)
    out_dir = Path(output_dir) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    result_data = {
        "topic": topic,
        "channels": channels,
        "statuses": publish_statuses,
    }
    out_path = out_dir / "publish_result.json"
    out_path.write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    all_failed = all("❌" in s or "⚠️" in s for s in publish_statuses) if publish_statuses else True
    if all_failed and has_fatal:
        return StageResult.fail(
            "\n".join(publish_statuses),
            failure_mode="fatal",
        )
    if all_failed:
        return StageResult.fail("\n".join(publish_statuses))

    return StageResult.success(
        result_data,
        output_path=str(out_path),
        files=generated_files,
    )
