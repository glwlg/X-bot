"""Write stage – generates article content from research material."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.config import get_client_for_model
from core.model_config import resolve_models_config_path, select_model_for_role
from services.openai_adapter import generate_text

from ap_utils import (
    MAX_SEARCH_CONTEXT_CHARS,
    normalize_article_data,
    parse_article_json,
    read_local_material_context,
    topic_slug,
)
from ap_stages import StageResult

logger = logging.getLogger(__name__)


async def write_stage(
    *,
    topic: str,
    source_path: str | None = None,
    research_data: dict[str, Any] | None = None,
    output_dir: str,
    word_count: int = 1000,
) -> StageResult:
    """Run the write stage.

    Accepts EITHER:
    - ``research_data`` dict (from search stage output), OR
    - ``source_path`` pointing to a ``.json`` / ``.md`` / ``.txt`` file.

    Returns a ``StageResult`` with the generated ``article.json``.
    """
    search_context = ""

    # -- resolve source --------------------------------------------------------
    if source_path and not research_data:
        src = Path(source_path)
        if not src.exists():
            return StageResult.fail(f"Source file not found: {source_path}")

        if src.suffix.lower() == ".json":
            try:
                research_data = json.loads(src.read_text(encoding="utf-8"))
            except Exception as exc:
                return StageResult.fail(f"Invalid research JSON: {exc}")
        else:
            # .md / .txt – treat as raw material
            try:
                search_context = read_local_material_context([src])
            except Exception as exc:
                return StageResult.fail(f"素材读取失败: {exc}")

    if research_data:
        search_context = str(research_data.get("context") or "").strip()
        if not search_context:
            # reconstruct from sources
            parts = []
            for src in list(research_data.get("sources") or []):
                content = str(src.get("content") or "").strip()
                if content:
                    parts.append(content)
            search_context = "\n---\n".join(parts)
        if not topic:
            topic = str(research_data.get("topic") or "").strip()

    if not search_context:
        return StageResult.fail("无写作素材输入")

    if not topic:
        topic = "未命名主题"

    # -- generate article ------------------------------------------------------
    try:
        article_data = await _generate_article_json(topic, search_context, word_count)
    except Exception as exc:
        logger.error("Article generation failed: %s", exc, exc_info=True)
        return StageResult.fail(f"创作失败: {exc}")

    # -- validate --------------------------------------------------------------
    title = str(article_data.get("title") or "").strip()
    sections = list(article_data.get("sections") or [])
    total_chars = sum(
        len(str(s.get("content") or "")) for s in sections
    )
    if not title:
        return StageResult.fail("文章标题为空，生成失败")
    if not sections:
        return StageResult.fail("文章无正文段落，生成失败")
    if total_chars < 200:
        return StageResult.fail(f"文章正文过短 ({total_chars} 字)，生成质量不足")

    # -- save ------------------------------------------------------------------
    slug = topic_slug(topic)
    out_dir = Path(output_dir) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "article.json"
    out_path.write_text(
        json.dumps(article_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return StageResult.success(article_data, output_path=str(out_path))


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

async def _generate_article_json(topic: str, search_context: str, word_count: int = 1000) -> dict[str, Any]:
    structure_prompt = (
        f"你是一位经验丰富的资深内容创作者，擅长根据主题和素材撰写高质量的深度长文。"
        f"请基于以下素材，为主题「{topic}」撰写一篇深度文章。\n\n"
        f"素材内容：\n{search_context[:MAX_SEARCH_CONTEXT_CHARS]}\n\n"
        "**风格要求**：\n"
        "- 根据主题自动选择最合适的写作风格（科技评论、行业分析、人物故事、知识科普、观点评论等）。\n"
        "- 文字要有洞察力和观点，拒绝平铺直叙和流水账。\n"
        "- 语言自然流畅，像与读者对话，避免生硬的学术腔或 AI 味。\n\n"
        "**篇幅要求**：\n"
        f"- 正文总字数要求约 {word_count} 字。\n"
        "- 拆分为 4 到 6 个 section，每个 section 有独立小标题。\n"
        "- 每段控制在 2-3 句话（约 80-120 字），然后换段，保持阅读节奏。\n\n"
        "**排版要求**：\n"
        "- 正文使用 HTML 标签排版，不要用 Markdown。\n"
        "- 每个 section 以 <h2> 小标题开头。\n"
        "- 正文段落使用 <p>，段与段之间自然分隔。\n"
        "- 适当使用 <blockquote> 做金句或观点提炼。\n"
        "- 可使用 <ul>/<li> 做列举，<b> 做关键词加粗。\n"
        '- 在每个 section 末尾加一行 <p style="margin-bottom:1.5em;"></p> 作为段间留白。\n\n'
        "**配图要求**：\n"
        "- 必须设计 1 张封面图 PROMPT（cover_prompt）。\n"
        "- 在 2-3 个 section 中设计 image_prompt（正文插图），其余为 null。\n"
        "- 所有图片 PROMPT 使用英文描述，适合 AI 图片生成。\n\n"
        "**输出格式**：\n"
        "- 返回严格 JSON 格式，仅返回 JSON 对象本身。\n"
        "- 不要 ```json 包裹，不要解释性文字。\n"
        "- JSON 必须使用双引号，结构如下：\n"
        "{\n"
        '  "title": "吸引力强但不标题党的标题",\n'
        '  "author": "笔名",\n'
        '  "digest": "100-150字摘要，概括核心观点",\n'
        '  "cover_prompt": "English prompt for cover image, 16:9 aspect ratio, professional editorial style",\n'
        '  "sections": [\n'
        '    { "content": "<h2>第一部分标题</h2><p>段落一正文...</p><p>段落二正文...</p>'
        '<p style=\\"margin-bottom:1.5em;\\"></p>", '
        '"image_prompt": "English prompt for inline image (16:9) or null" },\n'
        '    { "content": "<h2>第二部分标题</h2><p>段落一正文...</p><p>段落二正文...</p>'
        '<p style=\\"margin-bottom:1.5em;\\"></p>", "image_prompt": null }\n'
        "  ]\n"
        "}"
    )

    model_to_use = select_model_for_role("primary")
    if not model_to_use:
        raise RuntimeError(
            f"No text model configured in {resolve_models_config_path()}"
        )
    async_client = get_client_for_model(model_to_use, is_async=True)
    if async_client is None:
        raise RuntimeError("OpenAI async client is not initialized")

    response_text = await generate_text(
        async_client=async_client,
        model=model_to_use,
        contents=structure_prompt,
        config={"response_mime_type": "application/json"},
    )
    return normalize_article_data(
        parse_article_json(str(response_text or "")),
        topic,
    )
