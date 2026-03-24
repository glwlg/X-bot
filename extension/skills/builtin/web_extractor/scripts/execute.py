from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

from services.web_summary_service import fetch_webpage_content

logger = logging.getLogger(__name__)


async def execute(ctx: UnifiedContext, params: dict, runtime=None):
    url = params.get("url", "").strip()

    if not url:
        yield {"text": "❌ 请提供目标网页的 URL (参数名: url)", "ui": {}}
        return

    logger.info("[web_extractor] start reading URL: %s", url)
    yield f"🌐 正在使用 Jina Reader 提取网页内容：{url}"

    try:
        content = await fetch_webpage_content(url)

        if content:
            # Yield full content back to the AI for its own analysis
            yield {
                "text": f"✅ 网页内容提取成功\n\n```markdown\n{content}\n```\n",
                "ui": {},
            }
        else:
            yield {
                "text": f"❌ 无法提取网页内容，请检查链接是否可访问：{url}",
                "ui": {},
            }
    except Exception as e:
        logger.error(f"[web_extractor] Failed to read {url}: {e}")
        yield {"text": f"❌ 读取网页时发生内部错误：{e}", "ui": {}}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Web extractor skill CLI bridge.",
    )
    add_common_arguments(parser)
    parser.add_argument("url", help="Target URL to extract")
    return parser


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    params = merge_params(args, {"url": str(args.url or "").strip()})
    return await run_execute_cli(execute, args=args, params=params)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
