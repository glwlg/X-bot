import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.skill_loader import skill_loader


@dataclass
class ExtensionCandidate:
    name: str
    description: str
    tool_name: str
    input_schema: Dict[str, Any]
    schema_summary: str
    triggers: List[str] = field(default_factory=list)


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]+", text.lower()) if len(tok) > 1}


class ExtensionRouter:
    """Lightweight deterministic router for short-lived extension injection."""

    def route(self, user_text: str, max_candidates: int = 3) -> List[ExtensionCandidate]:
        if not user_text or not user_text.strip():
            return []

        query = user_text.lower().strip()
        query_tokens = _tokenize(query)
        news_intent = any(
            token in query for token in ("新闻", "头条", "热点", "快讯", "最新", "今天", "今日", "news")
        )
        writing_intent = any(
            token in query for token in ("写", "文章", "稿", "公众号", "发布", "报道", "newsletter")
        )

        scored: List[tuple[float, Dict[str, Any]]] = []
        for skill in skill_loader.get_skills_summary():
            name = skill.get("name", "")
            if not name:
                continue

            description = skill.get("description", "")
            triggers = skill.get("triggers", []) or []
            input_schema = skill.get("input_schema", {}) or {"type": "object", "properties": {}}

            score = 0.0
            lowered_name = name.lower()
            lowered_desc = description.lower()

            if lowered_name in query:
                score += 5.0
            elif query in lowered_name:
                score += 2.0

            name_tokens = _tokenize(lowered_name)
            desc_tokens = _tokenize(lowered_desc)
            trigger_tokens = set()
            for trig in triggers:
                trigger_tokens |= _tokenize(str(trig))
                trigger_text = str(trig).lower().strip()
                if trigger_text and (trigger_text in query or query in trigger_text):
                    score += 3.0

            score += len(query_tokens & name_tokens) * 2.0
            score += len(query_tokens & trigger_tokens) * 1.5
            score += len(query_tokens & desc_tokens) * 0.8

            # Lightweight Chinese fallback: allow partial keyword overlap.
            common_keywords = ("研究", "调研", "分析", "报告", "部署", "下载", "订阅")
            for kw in common_keywords:
                if kw in query and (kw in lowered_name or kw in lowered_desc):
                    score += 0.8

            # News/realtime intent: strongly prefer search/research tools.
            if news_intent:
                if lowered_name in {"searxng_search", "deep_research", "rss_subscribe"}:
                    score += 3.2
                elif lowered_name == "web_browser":
                    score += 2.0
                elif lowered_name == "news_article_writer":
                    if writing_intent:
                        score += 1.5
                    else:
                        score -= 2.8
                if "search" in lowered_name or "searxng" in lowered_name:
                    score += 1.0

            # Skip weak/noisy matches.
            if score < 1.5:
                continue

            scored.append(
                (
                    score,
                    {
                        "name": name,
                        "description": description,
                        "input_schema": input_schema,
                        "triggers": triggers,
                    },
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[: max(0, min(3, max_candidates))]

        results: List[ExtensionCandidate] = []
        for _, item in selected:
            name = item["name"]
            safe = name.replace("-", "_")
            schema = item["input_schema"]
            props = list((schema.get("properties") or {}).keys())
            required = list(schema.get("required") or [])
            schema_summary = f"required={required}, fields={props[:8]}"
            results.append(
                ExtensionCandidate(
                    name=name,
                    description=item.get("description", ""),
                    tool_name=f"ext_{safe}",
                    input_schema=schema,
                    schema_summary=schema_summary,
                    triggers=item.get("triggers", []) or [],
                )
            )

        return results
