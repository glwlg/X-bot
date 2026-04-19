"""Article Publisher – orchestrator + CLI entry point.

This is the single ``execute.py`` required by the skill system.  Internally
it delegates to four composable stages under ``stages/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
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
from core.app_paths import data_dir

prepare_default_env(REPO_ROOT)

from extension.skills.builtin.credential_manager.scripts.store import get_credential_entry

from ap_stages.illustrate import illustrate_stage
from ap_stages.publish import publish_stage
from ap_stages.search import search_stage
from ap_stages.write import write_stage
from ap_utils import (
    as_bool,
    build_article_preview,
    build_news_rejection_message,
    derive_topic_requirements,
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
TIME_TEXT_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
WECHAT_ACCOUNT_NAME_RE = re.compile(
    r"(?:发布到|发到|投递到|同步到)(?P<name>[\w\-\u4e00-\u9fff]{2,32})公众号"
)
GENERIC_WECHAT_ACCOUNT_NAMES = {"微信", "公众", "官方", "这个", "该", "默认", "目标"}


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

def _resolve_article_output_dir(params: dict[str, Any]) -> str:
    """Return the base output directory for article artefacts."""
    explicit = str(params.get("output_dir") or "").strip()
    if explicit:
        return explicit
    return str(
        (
            data_dir() / "user" / "skills" / "article_publisher" / "articles"
        ).resolve()
    )


async def _resolve_current_date(ctx: UnifiedContext, topic: str) -> str:
    requirements = derive_topic_requirements(topic)
    if not requirements["same_day_only"]:
        return ""

    explicit_date = str(requirements.get("current_date") or "").strip()
    if explicit_date:
        return explicit_date

    fallback = datetime.now().strftime("%Y-%m-%d")
    try:
        result = await ctx.run_skill("daily_query", {"query_type": "time"})
    except Exception as exc:
        logger.warning("daily_query skill call failed: %s", exc)
        return fallback

    if isinstance(result, dict):
        match = TIME_TEXT_DATE_RE.search(str(result.get("text") or ""))
        if match:
            return match.group(1)
    return fallback


def _resolve_wechat_account_selector(params: dict[str, Any], topic: str) -> str:
    for key in ("wechat_account", "wechat_account_name", "official_account", "official_account_name"):
        value = str(params.get(key) or "").strip()
        if value:
            return value

    match = WECHAT_ACCOUNT_NAME_RE.search(str(topic or "").strip())
    if match:
        candidate = str(match.group("name") or "").strip()
        if candidate not in GENERIC_WECHAT_ACCOUNT_NAMES:
            return candidate
    return ""


def _resolve_news_rejection_message(topic: str, research_data: dict[str, Any]) -> str:
    if not isinstance(research_data, dict):
        return ""
    if str(research_data.get("source_type") or "").strip().lower() != "web":
        return ""

    validation = research_data.get("news_validation")
    if not isinstance(validation, dict) or not validation.get("recommend_reject"):
        return ""

    message = str(validation.get("reject_message") or "").strip()
    if message:
        return message

    current_date = str(research_data.get("current_date") or "").strip()
    requirements = derive_topic_requirements(topic, current_date=current_date)
    subject = str(research_data.get("subject") or requirements["subject"] or topic).strip()
    same_day_only = bool(validation.get("same_day_only") or requirements["same_day_only"])
    return build_news_rejection_message(subject, same_day_only=same_day_only)


async def _resolve_wechat_account(
    user_id: int | str,
    *,
    params: dict[str, Any],
    topic: str,
) -> tuple[dict[str, Any] | None, str]:
    selector = _resolve_wechat_account_selector(params, topic)
    entry = await get_credential_entry(
        user_id,
        "wechat_official_account",
        selector or None,
    )
    if not entry:
        return None, selector

    account = dict(entry.get("data") or {})
    account["credential_name"] = str(entry.get("name") or "").strip()
    account["credential_id"] = str(entry.get("id") or "").strip()
    return account, selector


# ---------------------------------------------------------------------------
# Full-flow orchestrator
# ---------------------------------------------------------------------------

async def _run_full_flow(
    ctx: UnifiedContext,
    params: dict[str, Any],
    *,
    topic: str,
    current_date: str,
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
        ctx,
        topic=topic,
        params=params,
        output_dir=output_dir,
        current_date=current_date,
    )
    if not search_result.ok:
        yield {
            "ok": False,
            "failure_mode": search_result.failure_mode,
            "text": f"❌ 搜索失败: {search_result.error}",
            "ui": {},
        }
        return

    news_rejection_message = _resolve_news_rejection_message(
        topic,
        search_result.data or {},
    )
    if news_rejection_message:
        yield {
            "ok": False,
            "failure_mode": "recoverable",
            "text": news_rejection_message,
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
        current_date=current_date,
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
    current_date: str,
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
            ctx,
            topic=topic,
            params=params,
            output_dir=output_dir,
            current_date=current_date,
        )

    elif stage == "write":
        yield "✍️ 正在写作..."
        result = await write_stage(
            topic=topic,
            source_path=source or None,
            output_dir=output_dir,
            word_count=word_count,
            current_date=current_date,
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

    if stage == "search":
        news_rejection_message = _resolve_news_rejection_message(
            topic,
            result.data or {},
        )
        if news_rejection_message:
            yield {
                "ok": False,
                "failure_mode": "recoverable",
                "text": news_rejection_message,
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

    wechat_account, wechat_selector = await _resolve_wechat_account(
        ctx.message.user.id,
        params=params,
        topic=topic,
    )
    accounts = {
        "wechat": wechat_account,
        "xiaohongshu": None,
    }

    if not topic and stage not in ("write", "illustrate", "publish"):
        yield {
            "ok": False,
            "failure_mode": "recoverable",
            "text": "❌ 请提供文章主题。",
            "ui": {},
        }
        return

    if wechat_selector and not wechat_account and (publish or "wechat" in publish_channels):
        yield {
            "ok": False,
            "failure_mode": "recoverable",
            "text": f"❌ 未找到公众号凭据：`{wechat_selector}`。",
            "ui": {},
        }
        return

    current_date = await _resolve_current_date(ctx, topic) if topic else ""
    output_dir = _resolve_article_output_dir(params)

    if stage:
        # Single-stage mode
        async for item in _run_single_stage(
            ctx, params,
            stage=stage,
            topic=topic,
            current_date=current_date,
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
            current_date=current_date,
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
        "--wechat-account",
        default="",
        help="Wechat official account credential name/id to use for publishing.",
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
        "wechat_account": str(getattr(args, "wechat_account", "") or "").strip() or None,
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
