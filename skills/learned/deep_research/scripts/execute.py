from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import httpx
from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from core.config import GEMINI_MODEL, openai_async_client
from services.openai_adapter import generate_text
from services.web_summary_service import fetch_webpage_content

logger = logging.getLogger(__name__)
_WEB_SEARCH_EXECUTE_MODULE = None


def _load_web_search_execute_module():
    global _WEB_SEARCH_EXECUTE_MODULE
    if _WEB_SEARCH_EXECUTE_MODULE is not None:
        return _WEB_SEARCH_EXECUTE_MODULE

    script_path = (
        REPO_ROOT / "skills" / "builtin" / "web_search" / "scripts" / "execute.py"
    )
    spec = importlib.util.spec_from_file_location(
        "xbot_builtin_web_search_execute",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("web_search execute module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _WEB_SEARCH_EXECUTE_MODULE = module
    return module


async def execute(ctx: UnifiedContext, params: dict, runtime=None):
    topic = params.get("topic", "").strip()
    depth = params.get("depth", 5)
    language = params.get("language", "zh-CN")
    logger.info(
        "[deep_research] execute called topic=%r depth=%r language=%r",
        topic,
        depth,
        language,
    )

    if not topic:
        yield {"text": "❌ 请提供研究主题 (topic)", "ui": {}}
        return

    depth = min(max(1, int(depth)), 10)  # 限制 1-5

    yield f"🧐 正在对 「{topic}」 进行深度研究 (深度: {depth})...\n此过程包含：搜索 -> 爬取网页 -> 深度阅读 -> 综合报告，可能需要 30-60 秒，请耐心等待。"

    # 1. Search Phase
    search_results = []
    try:
        web_search_execute = _load_web_search_execute_module()
        provider, _ = web_search_execute.build_fallback_provider_chain(queries=[topic])
        async with httpx.AsyncClient(timeout=30.0) as client:
            search_results = await provider.search(
                query_text=topic,
                categories_value="general,news,it,science",
                time_range="year",
                language=language,
                engines=[],
                client=client,
            )
        search_results = [
            item for item in list(search_results or []) if isinstance(item, dict)
        ][:depth]
    except Exception as e:
        logger.error(f"Search failed: {e}")
        yield f"⚠️ 搜索阶段出错: {e}"

    if not search_results:
        logger.info("[deep_research] no search results for topic=%r", topic)
        yield {"text": "❌ 未找到相关搜索结果，研究终止。", "ui": {}}
        return

    # 2. Crawl Phase
    yield f"🕷️ 正在爬取 {len(search_results)} 个网页..."

    async def process_url(item):
        url = item.get("url")
        title = item.get("title", "No Title")
        try:
            content = await fetch_webpage_content(url)
            if content:
                return {"title": title, "url": url, "content": content}
        except Exception as e:
            logger.error(f"Crawl failed for {url}: {e}")
        return None

    crawled_results = await asyncio.gather(
        *(process_url(item) for item in search_results)
    )
    valid_data = [item for item in crawled_results if item]
    logger.info(
        "[deep_research] crawled %s/%s usable pages",
        len(valid_data),
        len(search_results),
    )

    if not valid_data:
        yield {
            "text": "❌ 无法读取任何网页内容（可能是因为反爬虫或网络问题），研究终止。",
            "ui": {},
        }
        return

    # 3. Synthesis Phase
    yield f"🧠 已获取 {len(valid_data)} 份资料，正在综合分析并撰写报告..."

    # Construct Context
    context_text = f"Research Topic: {topic}\n\nSources Data:\n"
    for i, data in enumerate(valid_data, 1):
        context_text += f"\n--- Source {i}: {data['title']} ---\nURL: {data['url']}\nContent:\n{data['content'][:15000]}\n"  # Limit per page to avoid insanity

    prompt = f"""
    You are a Deep Research Analyst. Your task is to write a comprehensive Deep Dive Report on the topic: "{topic}".
    
    Based ONLY on the provided source materials below, write a detailed, structured, and professional report.
    
    Report Structure:
    1. **Executive Summary**: High-level overview of key findings.
    2. **Detailed Analysis**: Break down the topic into key aspects (e.g., Architecture, Performance, Pros/Cons, History).
    3. **Key Insights**: What are the most important takeaways?
    4. **Source Discrepancies** (if any): Did sources disagree?
    5. **Reference List**: List the titles and URLs of sources used.
    
    Format output as Markdown. Use proper Markdown heading hierarchy (# for title, ## for sections, etc.).
    Title the report "Deep Research: {topic}".
    Output ONLY the Markdown content, do NOT wrap it in code fences.
    
    Source Material:
    {context_text}
    """

    try:
        if openai_async_client is None:
            raise RuntimeError("OpenAI async client is not initialized")
        report_md = await generate_text(
            async_client=openai_async_client,
            model=GEMINI_MODEL,
            contents=prompt,
        )
        report_md = str(report_md or "")

        # Strip markdown code fences if AI wrapped them
        import re

        report_md = re.sub(r"^```(?:markdown|md)?\s*", "", report_md)
        report_md = re.sub(r"\s*```$", "", report_md)

        yield {
            "text": f"🔇🔇🔇【深度研究报告】\n\nSuccess: Deep research report generated for '{topic}' based on {len(valid_data)} sources.",
            "files": {"deep_research_report.md": report_md.encode("utf-8")},
            "ui": {},
        }
        logger.info(
            "[deep_research] report generated for topic=%r with %s sources",
            topic,
            len(valid_data),
        )
        return

    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        yield {"text": f"❌ 报告生成阶段失败: {e}", "ui": {}}
        return


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deep research skill CLI bridge.",
    )
    add_common_arguments(parser)
    parser.add_argument("topic", help="Research topic")
    parser.add_argument(
        "--depth",
        type=int,
        default=5,
        help="Research depth/page count, default 5",
    )
    parser.add_argument(
        "--language",
        default="zh-CN",
        help="Search language, default zh-CN",
    )
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    return merge_params(
        args,
        {
            "topic": str(args.topic or "").strip(),
            "depth": int(args.depth or 5),
            "language": str(args.language or "zh-CN").strip() or "zh-CN",
        },
    )


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
