import logging
from core.platform.models import UnifiedContext
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
