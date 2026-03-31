"""Article Publisher – orchestrator + CLI entry point.

This is the single ``execute.py`` required by the skill system.  Internally
it delegates to four composable stages under ``stages/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from extension.skills.builtin.credential_manager.scripts.store import get_credential

from ap_stages.illustrate import illustrate_stage
from ap_stages.publish import publish_stage
from ap_stages.search import search_stage
from ap_stages.write import write_stage
from ap_utils import (
    as_bool,
    build_article_preview,
    primary_author_account,
    resolve_article_author,
    resolve_local_material_paths,
    resolve_publish_channels,
    resolve_topic,
)
from ap_utils.xiaohongshu import (
    build_xiaohongshu_note_attachment,
    fallback_xiaohongshu_note,
    generate_xiaohongshu_note_json,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

def _resolve_article_output_dir(params: dict[str, Any]) -> str:
    """Return the base output directory for article artefacts."""
    explicit = str(params.get("output_dir") or "").strip()
    if explicit:
        return explicit
    data_dir = Path(str(os.getenv("DATA_DIR", "data") or "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = data_dir.resolve()
    return str((data_dir / "user" / "skills" / "article_publisher" / "articles").resolve())


# ---------------------------------------------------------------------------
# Full-flow orchestrator
# ---------------------------------------------------------------------------

async def _run_full_flow(
    ctx: UnifiedContext,
    params: dict[str, Any],
    *,
    topic: str,
    publish: bool,
    publish_channels: list[str],
    accounts: dict[str, dict[str, Any] | None],
    output_dir: str,
    word_count: int = 1000,
):
    """Run search → write → illustrate → (publish) and yield progress."""

    # Stage 1: Search
    yield "🔍 正在搜索并整理素材..."
    search_result = await search_stage(
        ctx, topic=topic, params=params, output_dir=output_dir,
    )
    if not search_result.ok:
        yield {
            "ok": False,
            "failure_mode": search_result.failure_mode,
            "text": f"❌ 搜索失败: {search_result.error}",
            "ui": {},
        }
        return

    # Stage 2: Write
    yield "✍️ 正在构思文章结构与配图设计..."
    write_result = await write_stage(
        topic=topic,
        research_data=search_result.data,
        output_dir=output_dir,
        word_count=word_count,
    )
    if not write_result.ok:
        yield {
            "ok": False,
            "failure_mode": write_result.failure_mode,
            "text": f"❌ 写作失败: {write_result.error}",
            "ui": {},
        }
        return

    article_data = write_result.data
    article_data["author"] = resolve_article_author(
        primary_author_account(accounts, publish_channels),
        fallback_author=str(article_data.get("author") or ""),
    )

    # Stage 3: Illustrate
    yield "🎨 正在并行绘制封面与插图..."
    illust_result = await illustrate_stage(
        ctx,
        topic=topic,
        article_data=article_data,
        author=str(article_data.get("author") or ""),
        output_dir=output_dir,
    )
    if not illust_result.ok:
        yield {
            "ok": False,
            "failure_mode": illust_result.failure_mode,
            "text": f"❌ 配图失败: {illust_result.error}",
            "ui": {},
        }
        return

    # Reconstruct image bytes from the illustrate result
    cover_bytes: bytes | None = None
    section_images: dict[int, bytes] = {}
    generated_files: dict[str, bytes | str] = dict(illust_result.files)
    for key, val in illust_result.files.items():
        if key.startswith("img_cover_") and isinstance(val, bytes):
            cover_bytes = val
        elif key.startswith("img_section_") and isinstance(val, bytes):
            # e.g. img_section_0.png → 0
            try:
                idx = int(key.split("_")[2].split(".")[0])
                section_images[idx] = val
            except (IndexError, ValueError):
                pass

    # Build preview
    preview_text = build_article_preview(
        article_data,
        cover_bytes=cover_bytes,
        section_images=section_images,
        publish=publish,
    )
    final_text = f"🔇🔇🔇【文章内容】\n\n{preview_text}".strip()
    publish_statuses: list[str] = []

    # Xiaohongshu note generation (even without publish, generate draft attachments)
    xiaohongshu_note_data: dict[str, Any] | None = None
    if "xiaohongshu" in publish_channels:
        yield "📝 正在生成小红书笔记版本..."
        try:
            xiaohongshu_note_data = await generate_xiaohongshu_note_json(topic, article_data)
        except Exception as exc:
            logger.warning("Xiaohongshu note generation failed, using fallback: %s", exc)
            xiaohongshu_note_data = fallback_xiaohongshu_note(topic, article_data)
        generated_files["xiaohongshu_note.txt"] = build_xiaohongshu_note_attachment(
            xiaohongshu_note_data
        )
        generated_files["xiaohongshu_note.json"] = json.dumps(
            xiaohongshu_note_data, ensure_ascii=False, indent=2,
        ).encode("utf-8")
        if not publish:
            publish_statuses.append("📝 已生成小红书发布草稿附件。")

    # Stage 4: Publish (optional)
    if publish:
        yield "📤 正在发布..."
        pub_result = await publish_stage(
            ctx,
            topic=topic,
            article_data=article_data,
            cover_bytes=cover_bytes,
            section_images=section_images,
            channels=publish_channels,
            accounts=accounts,
            output_dir=output_dir,
        )
        # merge files from publish stage
        generated_files.update(pub_result.files)
        if pub_result.ok and pub_result.data:
            publish_statuses.extend(pub_result.data.get("statuses") or [])
        elif not pub_result.ok:
            publish_statuses.append(f"❌ 发布失败: {pub_result.error}")

    if publish_statuses:
        final_text = f"{final_text}\n\n---\n" + "\n".join(publish_statuses)

    yield {
        "ok": True,
        "text": final_text,
        "files": generated_files,
        "ui": {},
        "task_outcome": "done",
        "terminal": True,
    }


# ---------------------------------------------------------------------------
# Single-stage runner
# ---------------------------------------------------------------------------

async def _run_single_stage(
    ctx: UnifiedContext,
    params: dict[str, Any],
    *,
    stage: str,
    topic: str,
    publish_channels: list[str],
    accounts: dict[str, dict[str, Any] | None],
    output_dir: str,
    word_count: int = 1000,
):
    """Run a single named stage and yield its result."""
    source = str(params.get("source") or params.get("source_path") or "").strip()

    if stage == "search":
        yield f"🔍 正在搜索 `{topic}` ..."
        result = await search_stage(
            ctx, topic=topic, params=params, output_dir=output_dir,
        )

    elif stage == "write":
        yield "✍️ 正在写作..."
        result = await write_stage(
            topic=topic, source_path=source or None, output_dir=output_dir, word_count=word_count,
        )

    elif stage == "illustrate":
        yield "🎨 正在生成配图..."
        article_data = None
        if source:
            src = Path(source)
            if src.exists() and src.suffix.lower() == ".json":
                article_data = json.loads(src.read_text(encoding="utf-8"))
        author = resolve_article_author(
            primary_author_account(accounts, publish_channels),
        )
        result = await illustrate_stage(
            ctx,
            topic=topic,
            source_path=source or None,
            article_data=article_data,
            author=author,
            output_dir=output_dir,
        )

    elif stage == "publish":
        yield "📤 正在发布..."
        result = await publish_stage(
            ctx,
            topic=topic,
            source_path=source or None,
            channels=publish_channels or ["wechat"],
            accounts=accounts,
            output_dir=output_dir,
        )

    else:
        yield {
            "ok": False,
            "failure_mode": "fatal",
            "text": f"❌ 未知阶段: {stage}",
            "ui": {},
        }
        return

    if not result.ok:
        yield {
            "ok": False,
            "failure_mode": result.failure_mode,
            "text": f"❌ {stage} 失败: {result.error}",
            "ui": {},
        }
        return

    text_parts = [f"✅ {stage} 完成"]
    if result.output_path:
        text_parts.append(f"输出: {result.output_path}")

    yield {
        "ok": True,
        "text": "\n".join(text_parts),
        "files": result.files,
        "ui": {},
        "task_outcome": "done",
        "terminal": True,
    }


# ---------------------------------------------------------------------------
# Skill entry point: execute()
# ---------------------------------------------------------------------------

async def execute(ctx: UnifiedContext, params: dict[str, Any], runtime=None):
    _ = runtime
    material_paths = resolve_local_material_paths(params)
    topic = resolve_topic(ctx, params, fallback_paths=material_paths)
    publish = as_bool(params.get("publish"), default=False)
    publish_channels = resolve_publish_channels(params)
    stage = str(params.get("stage") or "").strip().lower()

    try:
        word_count = int(params.get("word_count", 1000) or 1000)
    except (ValueError, TypeError):
        word_count = 1000

    wechat_account = await get_credential(ctx.message.user.id, "wechat_official_account")
    xiaohongshu_account = await get_credential(ctx.message.user.id, "xiaohongshu_publisher")
    accounts = {
        "wechat": wechat_account,
        "xiaohongshu": xiaohongshu_account,
    }

    if not topic and stage not in ("write", "illustrate", "publish"):
        yield {
            "ok": False,
            "failure_mode": "recoverable",
            "text": "❌ 请提供文章主题。",
            "ui": {},
        }
        return

    output_dir = _resolve_article_output_dir(params)

    if stage:
        # Single-stage mode
        async for item in _run_single_stage(
            ctx, params,
            stage=stage,
            topic=topic,
            publish_channels=publish_channels,
            accounts=accounts,
            output_dir=output_dir,
            word_count=word_count,
        ):
            yield item
    else:
        # Full-flow mode
        async for item in _run_full_flow(
            ctx, params,
            topic=topic,
            publish=publish,
            publish_channels=publish_channels,
            accounts=accounts,
            output_dir=output_dir,
            word_count=word_count,
        ):
            yield item


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a long-form article and optional multi-channel publishing outputs.",
    )
    parser.add_argument(
        "topic",
        nargs="*",
        help="Article topic. If omitted, --message-text or ctx.message.text is used.",
    )
    parser.add_argument(
        "--stage",
        choices=["search", "write", "illustrate", "publish"],
        default=None,
        help="Run a single stage instead of the full pipeline.",
    )
    parser.add_argument(
        "--source",
        default="",
        help="Source file for single-stage mode (research.json, article.json, etc.).",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the generated content to selected channels.",
    )
    parser.add_argument(
        "--publish-channel",
        action="append",
        default=[],
        help="Publish/output channel. Supported: wechat, xiaohongshu. Can be passed multiple times.",
    )
    parser.add_argument(
        "--source-path",
        action="append",
        default=[],
        help="Local markdown/txt material path. Can be passed multiple times.",
    )
    parser.add_argument(
        "--word-count",
        type=int,
        default=1000,
        help="Target word count for the article.",
    )
    add_common_arguments(parser)
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    topic = " ".join(str(item or "").strip() for item in list(args.topic or [])).strip()
    source_paths = [
        str(item or "").strip()
        for item in list(getattr(args, "source_path", []) or [])
        if str(item or "").strip()
    ]
    publish_channels = [
        str(item or "").strip()
        for item in list(getattr(args, "publish_channel", []) or [])
        if str(item or "").strip()
    ]
    explicit = {
        "topic": topic or None,
        "publish": bool(getattr(args, "publish", False)),
        "publish_channels": publish_channels or None,
        "source_paths": source_paths or None,
        "stage": getattr(args, "stage", None),
        "source": str(getattr(args, "source", "") or "").strip() or None,
        "word_count": getattr(args, "word_count", 1000),
    }
    return merge_params(args, explicit)


async def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
