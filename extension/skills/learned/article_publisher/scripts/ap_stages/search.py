"""Search stage – gathers research material from the web or local files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.platform.models import UnifiedContext
from services.web_summary_service import fetch_webpage_content

from ap_utils import (
    MAX_DOC_SNIPPET_CHARS,
    decode_text_file,
    extract_urls,
    read_local_material_context,
    resolve_local_material_paths,
    topic_slug,
)
from ap_stages import StageResult

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_RESULT_COUNT = 8
MAX_DEEP_READ_URLS = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_search_summary(search_res: Any) -> tuple[str, str]:
    if not isinstance(search_res, dict):
        return str(search_res or ""), ""

    text = str(search_res.get("text") or "")
    files = search_res.get("files")
    report_text = ""
    if isinstance(files, dict) and files.get("search_report.md") is not None:
        report_text = decode_text_file(files.get("search_report.md"))
    return text, report_text


async def _collect_search_context(
    ctx: UnifiedContext,
    *,
    topic: str,
) -> dict[str, Any]:
    try:
        search_res = await ctx.run_skill(
            "web_search",
            {"query": topic, "num_results": DEFAULT_SEARCH_RESULT_COUNT},
        )
    except Exception as exc:
        logger.warning("web_search skill call failed: %s", exc)
        return {"summary_text": "", "report_text": "", "urls": []}

    summary_text, report_text = _extract_search_summary(search_res)
    url_source = report_text or summary_text
    unique_urls = extract_urls(url_source)[:MAX_DEEP_READ_URLS]

    if unique_urls:
        return {
            "summary_text": summary_text,
            "report_text": report_text,
            "urls": unique_urls,
        }

    return {
        "summary_text": summary_text,
        "report_text": report_text,
        "urls": [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_stage(
    ctx: UnifiedContext,
    *,
    topic: str,
    params: dict[str, Any],
    output_dir: str,
) -> StageResult:
    """Run the search/research stage.

    If local material paths are present in *params*, reads them instead of
    doing a web search.  Returns a ``StageResult`` with *data* containing
    the ``research.json`` structure.
    """
    material_paths = resolve_local_material_paths(params)

    # -- local material path --------------------------------------------------
    if material_paths:
        try:
            local_context = read_local_material_context(material_paths)
        except Exception as exc:
            return StageResult.fail(f"本地素材读取失败: {exc}")

        research_data = {
            "topic": topic,
            "search_results": [],
            "sources": [{"url": str(p), "content": ""} for p in material_paths],
            "context": local_context,
            "source_type": "local",
        }
        out_path = _save_research(research_data, output_dir=output_dir, topic=topic)
        return StageResult.success(research_data, output_path=out_path)

    # -- web search path -------------------------------------------------------
    search_payload = await _collect_search_context(ctx, topic=topic)

    deep_read_urls = list(search_payload.get("urls") or [])
    search_context = ""
    if deep_read_urls:
        docs: list[str] = []
        for url in deep_read_urls:
            content = await fetch_webpage_content(url)
            if content:
                docs.append(f"Src: {url}\n{content[:MAX_DOC_SNIPPET_CHARS]}")
        if docs:
            search_context = "\n---\n".join(docs)

    if not search_context:
        search_context = str(search_payload.get("report_text") or "").strip()
    if not search_context:
        search_context = str(search_payload.get("summary_text") or "").strip()

    if not search_context:
        return StageResult.fail("未找到相关资料，请调整主题")

    research_data = {
        "topic": topic,
        "search_results": [],
        "sources": [{"url": u, "content": ""} for u in deep_read_urls],
        "context": search_context,
        "source_type": "web",
    }
    out_path = _save_research(research_data, output_dir=output_dir, topic=topic)
    return StageResult.success(research_data, output_path=out_path)


def _save_research(data: dict[str, Any], *, output_dir: str, topic: str) -> str:
    slug = topic_slug(topic)
    out_dir = Path(output_dir) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "research.json"
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(out_path)
