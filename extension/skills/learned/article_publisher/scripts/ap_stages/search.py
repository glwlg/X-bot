"""Search stage – gathers research material from the web or local files."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from core.platform.models import UnifiedContext
from services.web_summary_service import fetch_webpage_content

from ap_utils import (
    MAX_DOC_SNIPPET_CHARS,
    build_news_rejection_message,
    decode_text_file,
    derive_topic_requirements,
    extract_urls,
    filter_lines_by_forbidden_terms,
    read_local_material_context,
    resolve_local_material_paths,
    topic_slug,
)
from ap_stages import StageResult

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_RESULT_COUNT = 8
MAX_DEEP_READ_URLS = 3
MIN_NEWS_CANDIDATE_SOURCES = 2
SAME_DAY_RELATIVE_TIME_RE = re.compile(r"(?:\d+\s*(?:小时|分钟)前|刚刚)")


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
    current_date: str = "",
) -> dict[str, Any]:
    requirements = derive_topic_requirements(topic, current_date=current_date)
    search_params: dict[str, Any] = {
        "query": requirements["search_query"] or topic,
        "num_results": DEFAULT_SEARCH_RESULT_COUNT,
    }
    if requirements["prefer_news"]:
        search_params["categories"] = "news"
        search_params["time_range"] = "day" if requirements["same_day_only"] else "week"

    try:
        search_res = await ctx.run_skill(
            "web_search",
            search_params,
        )
    except Exception as exc:
        logger.warning("web_search skill call failed: %s", exc)
        return {
            "summary_text": "",
            "report_text": "",
            "all_urls": [],
            "urls": [],
            "query": search_params["query"],
        }

    summary_text, report_text = _extract_search_summary(search_res)
    url_source = report_text or summary_text
    all_urls = extract_urls(url_source)
    deep_read_urls = all_urls[:MAX_DEEP_READ_URLS]

    if deep_read_urls:
        return {
            "summary_text": summary_text,
            "report_text": report_text,
            "all_urls": all_urls,
            "urls": deep_read_urls,
            "query": search_params["query"],
        }

    return {
        "summary_text": summary_text,
        "report_text": report_text,
        "all_urls": all_urls,
        "urls": [],
        "query": search_params["query"],
    }


def _contains_same_day_hint(text: str, current_date: str) -> bool:
    raw = str(text or "")
    if not raw:
        return False

    marker_candidates: list[str] = ["今日", "今天", "当日"]
    date_token = str(current_date or "").strip()
    if date_token:
        marker_candidates.append(date_token)
        parts = date_token.split("-")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])
            marker_candidates.extend(
                [
                    f"{year}年{month}月{day}日",
                    f"{month}月{day}日",
                ]
            )

    for marker in marker_candidates:
        if marker and marker in raw:
            return True
    if SAME_DAY_RELATIVE_TIME_RE.search(raw):
        return True
    return False


def _build_news_validation(
    *,
    requirements: dict[str, Any],
    search_payload: dict[str, Any],
    search_context: str,
    current_date: str,
    usable_source_count: int,
    same_day_evidence_count: int,
    source_type: str,
) -> dict[str, Any]:
    is_news_request = bool(requirements["prefer_news"])
    same_day_only = bool(requirements["same_day_only"])
    candidate_source_count = len(list(search_payload.get("all_urls") or []))
    has_context = bool(str(search_context or "").strip())
    suspected_same_day = bool(
        same_day_evidence_count > 0
        or (same_day_only and _contains_same_day_hint(search_context, current_date))
    )

    reject_reasons: list[str] = []
    has_enough_news = True
    recommend_reject = False

    if is_news_request and source_type == "web":
        if not has_context:
            reject_reasons.append("未抓取到可写的新闻正文素材")
        if candidate_source_count < MIN_NEWS_CANDIDATE_SOURCES:
            reject_reasons.append(
                f"候选新闻来源不足（当前 {candidate_source_count} 条，至少 {MIN_NEWS_CANDIDATE_SOURCES} 条）"
            )
        if same_day_only and not suspected_same_day:
            reject_reasons.append("未发现可确认的当天新闻线索")
        has_enough_news = not reject_reasons
        recommend_reject = not has_enough_news

    reject_message = ""
    if recommend_reject:
        reject_message = build_news_rejection_message(
            str(requirements["subject"] or ""),
            same_day_only=same_day_only,
        )

    return {
        "is_news_request": is_news_request,
        "prefer_news": bool(requirements["prefer_news"]),
        "same_day_only": same_day_only,
        "has_enough_news": has_enough_news,
        "candidate_source_count": candidate_source_count,
        "usable_source_count": int(usable_source_count),
        "suspected_same_day": suspected_same_day,
        "same_day_evidence_count": int(same_day_evidence_count),
        "recommend_reject": recommend_reject,
        "reject_reasons": reject_reasons,
        "reject_message": reject_message,
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
    current_date: str = "",
) -> StageResult:
    """Run the search/research stage.

    If local material paths are present in *params*, reads them instead of
    doing a web search.  Returns a ``StageResult`` with *data* containing
    the ``research.json`` structure.
    """
    material_paths = resolve_local_material_paths(params)
    requirements = derive_topic_requirements(topic, current_date=current_date)
    effective_topic = str(requirements["subject"] or topic).strip() or topic

    # -- local material path --------------------------------------------------
    if material_paths:
        try:
            local_context = read_local_material_context(material_paths)
        except Exception as exc:
            return StageResult.fail(f"本地素材读取失败: {exc}")

        news_validation = _build_news_validation(
            requirements=requirements,
            search_payload={"all_urls": []},
            search_context=local_context,
            current_date=current_date,
            usable_source_count=len(material_paths),
            same_day_evidence_count=0,
            source_type="local",
        )

        research_data = {
            "topic": topic,
            "subject": effective_topic,
            "search_results": [],
            "sources": [{"url": str(p), "content": ""} for p in material_paths],
            "context": local_context,
            "source_type": "local",
            "news_validation": news_validation,
        }
        out_path = _save_research(research_data, output_dir=output_dir, topic=effective_topic)
        return StageResult.success(research_data, output_path=out_path)

    # -- web search path -------------------------------------------------------
    search_payload = await _collect_search_context(
        ctx,
        topic=topic,
        current_date=current_date,
    )

    deep_read_urls = list(search_payload.get("urls") or [])
    search_context = ""
    same_day_evidence_count = 0
    usable_source_count = 0
    if deep_read_urls:
        docs: list[str] = []
        for url in deep_read_urls:
            content = await fetch_webpage_content(url)
            if content:
                if any(term and (term in url or term in content) for term in requirements["forbidden_terms"]):
                    continue
                usable_source_count += 1
                if _contains_same_day_hint(f"{url}\n{content}", current_date):
                    same_day_evidence_count += 1
                docs.append(f"Src: {url}\n{content[:MAX_DOC_SNIPPET_CHARS]}")
        if docs:
            search_context = "\n---\n".join(docs)

    if not search_context:
        search_context = filter_lines_by_forbidden_terms(
            str(search_payload.get("report_text") or "").strip(),
            requirements["forbidden_terms"],
        )
    if not search_context:
        search_context = filter_lines_by_forbidden_terms(
            str(search_payload.get("summary_text") or "").strip(),
            requirements["forbidden_terms"],
        )

    if not same_day_evidence_count and _contains_same_day_hint(search_context, current_date):
        same_day_evidence_count = 1

    news_validation = _build_news_validation(
        requirements=requirements,
        search_payload=search_payload,
        search_context=search_context,
        current_date=current_date,
        usable_source_count=usable_source_count,
        same_day_evidence_count=same_day_evidence_count,
        source_type="web",
    )

    if not search_context and not requirements["prefer_news"]:
        if requirements["forbidden_terms"]:
            return StageResult.fail("排除指定内容后未找到合适资料，请调整主题")
        return StageResult.fail("未找到相关资料，请调整主题")

    research_data = {
        "topic": topic,
        "subject": effective_topic,
        "current_date": current_date,
        "search_query": str(search_payload.get("query") or requirements["search_query"] or "").strip(),
        "search_results": [],
        "sources": [{"url": u, "content": ""} for u in deep_read_urls],
        "context": search_context,
        "source_type": "web",
        "news_validation": news_validation,
    }
    out_path = _save_research(research_data, output_dir=output_dir, topic=effective_topic)
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
