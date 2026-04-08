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
    article_plain_text,
    build_news_rejection_message,
    detect_forced_news_fillers,
    derive_topic_requirements,
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
    current_date: str = "",
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
        if not current_date:
            current_date = str(research_data.get("current_date") or "").strip()

    if not topic:
        topic = "未命名主题"
    requirements = derive_topic_requirements(topic, current_date=current_date)

    if (
        research_data
        and str(research_data.get("source_type") or "").strip().lower() == "web"
    ):
        news_validation = research_data.get("news_validation")
        if isinstance(news_validation, dict) and news_validation.get("recommend_reject"):
            reject_message = str(news_validation.get("reject_message") or "").strip()
            if not reject_message:
                reject_message = build_news_rejection_message(
                    str(research_data.get("subject") or requirements["subject"] or topic),
                    same_day_only=bool(
                        news_validation.get("same_day_only") or requirements["same_day_only"]
                    ),
                )
            return StageResult.fail(reject_message)

    if not search_context:
        return StageResult.fail("无写作素材输入")

    # -- generate article ------------------------------------------------------
    try:
        article_data = await _generate_article_json(
            topic,
            search_context,
            word_count,
            current_date=current_date,
        )
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

    if requirements["prefer_news"]:
        combined_text = "\n".join(
            [
                str(article_data.get("title") or ""),
                str(article_data.get("digest") or ""),
                article_plain_text(article_data),
            ]
        )
        filler_hits = detect_forced_news_fillers(combined_text)
        if filler_hits:
            return StageResult.fail(
                "写作结果包含不允许的新闻硬凑表述："
                + "、".join(filler_hits)
                + "。请补充有效新闻素材后再试。"
            )

    # -- save ------------------------------------------------------------------
    effective_topic = str(derive_topic_requirements(topic, current_date=current_date)["subject"] or topic).strip() or topic
    slug = topic_slug(effective_topic)
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

async def _generate_article_json(
    topic: str,
    search_context: str,
    word_count: int = 1000,
    *,
    current_date: str = "",
) -> dict[str, Any]:
    requirements = derive_topic_requirements(topic, current_date=current_date)
    subject = str(requirements["subject"] or topic).strip()

    brief_lines = [
        f"- 主题：{subject}",
        "- 面向公众号普通读者，语言自然、清楚、可直接发布。",
    ]
    if requirements["prefer_news"]:
        brief_lines.append("- 按新闻综述写作，先交代事实，再说明值得关注的原因。")
        brief_lines.append(
            "- 严禁出现“没有新官宣但… / 虽然没有官宣… / 值得关注的是行业信号…”这类硬凑表述。"
        )
    if requirements["same_day_only"] and requirements["current_date"]:
        brief_lines.append(
            f"- 只使用 {requirements['current_date']} 当天的信息；素材不足时必须克制，不得硬凑。"
        )
    if requirements["body_only"] or requirements["public_readers"]:
        brief_lines.append(
            "- 只输出正文，不要写导语说明、免责声明、END、图片来源、责编、关注提示等非正文内容。"
        )
    if requirements["forbidden_terms"]:
        brief_lines.append(
            "- 不要出现以下对象或相关内容："
            + "、".join(requirements["forbidden_terms"])
            + "。"
        )

    structure_prompt = (
        "你是一名资深中文公众号编辑，擅长把公开素材整理成适合直接发布的图文文章。"
        f"请基于以下素材，围绕主题「{subject}」完成写作。\n\n"
        "写作要求：\n"
        + "\n".join(brief_lines)
        + "\n\n"
        f"素材内容：\n{search_context[:MAX_SEARCH_CONTEXT_CHARS]}\n\n"
        "**风格要求**：\n"
        "- 用中文写作，语气清楚、自然、克制，像成熟公众号编辑在向普通读者解释新闻。\n"
        "- 观点可以有，但必须建立在素材事实之上，不要脱离素材做空泛延展。\n"
        "- 开头直接进入主题，不绕圈子，不写自我介绍，不写创作说明。\n\n"
        "**篇幅要求**：\n"
        f"- 正文总字数要求约 {word_count} 字。\n"
        "- 拆分为 4 到 6 个 section，每个 section 有独立小标题。\n"
        "- 每段控制在 2-3 句话（约 80-120 字），然后换段，保持阅读节奏。\n"
        "- 结尾要回到读者价值，总结这些新闻对行业和普通人的实际意义，不要写空泛口号。\n\n"
        "**排版要求**：\n"
        "- 正文使用 HTML 标签排版，不要用 Markdown。\n"
        "- 每个 section 以 <h2> 小标题开头。\n"
        "- 正文段落使用 <p>，段与段之间自然分隔。\n"
        "- 可使用 <ul>/<li> 做列举，<b> 做关键词加粗，但不要堆砌格式。\n"
        '- 在每个 section 末尾加一行 <p style="margin-bottom:1.5em;"></p> 作为段间留白。\n\n'
        "**配图要求**：\n"
        "- 必须设计 1 张封面图 PROMPT（cover_prompt）。\n"
        "- 在 1-3 个 section 中设计 image_prompt（正文插图），其余为 null。\n"
        "- 每个 image_prompt 必须服务对应 section 的事实内容，禁止为了凑图生成无关泛图。\n"
        "- 所有图片 PROMPT 使用英文描述，适合 AI 图片生成。\n\n"
        "**输出格式**：\n"
        "- 返回严格 JSON 格式，仅返回 JSON 对象本身。\n"
        "- 不要 ```json 包裹，不要解释性文字。\n"
        "- JSON 必须使用双引号，结构如下：\n"
        "{\n"
        '  "title": "信息明确、适合公众号的标题",\n'
        '  "author": "笔名",\n'
        '  "digest": "100-150字摘要，概括今天这篇文章告诉读者什么",\n'
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
