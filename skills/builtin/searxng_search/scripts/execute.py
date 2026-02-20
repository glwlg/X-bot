import asyncio
import os
import re
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from core.platform.models import UnifiedContext

MAX_QUERIES = 5
MAX_RESULTS = 10
DEFAULT_INTENT_PROFILE = "general"

WEATHER_INTENT_KEYWORDS = (
    "天气",
    "气温",
    "体感",
    "降水",
    "风力",
    "风向",
    "空气质量",
    "aqi",
    "pm2.5",
    "紫外线",
    "weather",
    "forecast",
)

NEWS_INTENT_KEYWORDS = (
    "新闻",
    "头条",
    "快讯",
    "时政",
    "财经新闻",
    "要闻",
    "breaking",
    "headline",
    "latest news",
)

TECH_INTENT_KEYWORDS = (
    "报错",
    "错误",
    "异常",
    "debug",
    "bug",
    "exception",
    "stack trace",
    "api",
    "sdk",
    "文档",
    "教程",
    "github",
    "stackoverflow",
    "python",
    "javascript",
    "typescript",
)

INTENT_KEYWORDS_BY_PROFILE: dict[str, tuple[str, ...]] = {
    "weather": WEATHER_INTENT_KEYWORDS,
    "news": NEWS_INTENT_KEYWORDS,
    "tech": TECH_INTENT_KEYWORDS,
}

WEATHER_PRIORITY_SCORES = {
    "weather.com.cn": 120,
    "nmc.cn": 100,
    "cma.cn": 95,
    "iqair.cn": 90,
    "qweather.com": 70,
}

NEWS_PRIORITY_SCORES = {
    "xinhuanet.com": 120,
    "people.com.cn": 110,
    "cctv.com": 100,
    "reuters.com": 95,
    "bbc.com": 90,
}

TECH_PRIORITY_SCORES = {
    "docs.python.org": 120,
    "developer.mozilla.org": 110,
    "stackoverflow.com": 100,
    "github.com": 90,
    "pypi.org": 80,
    "npmjs.com": 80,
}

PROFILE_PRIORITY_SCORES: dict[str, dict[str, int]] = {
    "weather": WEATHER_PRIORITY_SCORES,
    "news": NEWS_PRIORITY_SCORES,
    "tech": TECH_PRIORITY_SCORES,
}

WEATHER_LOW_PRIORITY_SCORES = {
    "baike.baidu.com": -100,
    "iqiyi.com": -80,
    "5adanci.com": -80,
    "sohu.com": -30,
}

NEWS_LOW_PRIORITY_SCORES = {
    "baike.baidu.com": -100,
    "zhidao.baidu.com": -80,
    "iqiyi.com": -80,
}

TECH_LOW_PRIORITY_SCORES = {
    "baike.baidu.com": -90,
    "zhidao.baidu.com": -80,
    "iqiyi.com": -80,
    "5adanci.com": -70,
}

PROFILE_LOW_PRIORITY_SCORES: dict[str, dict[str, int]] = {
    "weather": WEATHER_LOW_PRIORITY_SCORES,
    "news": NEWS_LOW_PRIORITY_SCORES,
    "tech": TECH_LOW_PRIORITY_SCORES,
}

INTENT_PROFILE_ALIASES = {
    "weather": "weather",
    "meteo": "weather",
    "forecast": "weather",
    "news": "news",
    "headline": "news",
    "tech": "tech",
    "technology": "tech",
    "technical": "tech",
    "dev": "tech",
    "developer": "tech",
    "general": "general",
    "default": "general",
}


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        rows = re.split(r"[,;\n\t]", value)
    elif isinstance(value, (list, tuple, set)):
        rows = [str(item or "") for item in value]
    else:
        return []

    output: list[str] = []
    for raw in rows:
        token = str(raw or "").strip()
        if not token or token in output:
            continue
        output.append(token)
    return output


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    rendered = str(value).strip().lower()
    if rendered in {"1", "true", "yes", "on", "y"}:
        return True
    if rendered in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _normalize_base_url(base_url: str) -> str:
    rendered = str(base_url or "").strip()
    if not rendered:
        return ""
    if rendered.endswith("/search"):
        return rendered
    if rendered.endswith("/"):
        return rendered + "search"
    return rendered + "/search"


def _domain_from_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    hostname = str(parsed.hostname or "").strip().lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _domain_matches_rule(domain: str, rule: str) -> bool:
    host = str(domain or "").strip().lower()
    pattern = str(rule or "").strip().lower().lstrip(".")
    if not host or not pattern:
        return False
    return host == pattern or host.endswith("." + pattern)


def _matches_any_rule(domain: str, rules: list[str]) -> bool:
    return any(_domain_matches_rule(domain, rule) for rule in rules)


def _merge_unique(base: list[str], additions: list[str]) -> list[str]:
    output = list(base)
    for item in additions:
        token = str(item or "").strip()
        if not token or token in output:
            continue
        output.append(token)
    return output


def _contains_intent_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(str(keyword or "").lower() in lowered for keyword in keywords)


def _normalize_intent_profile(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return ""
    return str(INTENT_PROFILE_ALIASES.get(key, "")).strip().lower()


def _detect_intent_profile(queries: list[str]) -> str:
    for query in queries:
        if _contains_intent_keyword(query, WEATHER_INTENT_KEYWORDS):
            return "weather"
    for query in queries:
        if _contains_intent_keyword(query, NEWS_INTENT_KEYWORDS):
            return "news"
    for query in queries:
        if _contains_intent_keyword(query, TECH_INTENT_KEYWORDS):
            return "tech"
    return DEFAULT_INTENT_PROFILE


def _profile_env_key(profile: str, suffix: str) -> str:
    return f"SEARXNG_SEARCH_{str(profile or '').strip().upper()}_{str(suffix or '').strip().upper()}"


def _load_intent_profile_settings(profile: str) -> dict[str, Any]:
    name = _normalize_intent_profile(profile) or DEFAULT_INTENT_PROFILE
    default_categories = "general"
    default_engines = ""
    default_allowlist = ""
    if name == "news":
        default_categories = "news"
        default_engines = "google,bing"
        default_allowlist = "xinhuanet.com,people.com.cn,cctv.com,reuters.com"
    if name == "tech":
        default_categories = "it"
        default_engines = "google,bing"
        default_allowlist = (
            "docs.python.org,developer.mozilla.org,stackoverflow.com,github.com"
        )
    if name == "weather":
        default_engines = "google,bing"
        default_allowlist = "weather.com.cn,iqair.cn,nmc.cn,cma.cn"
    default_time_range = "day" if name in {"weather", "news"} else ""

    categories = str(os.getenv(_profile_env_key(name, "CATEGORIES"), "")).strip()
    if not categories:
        categories = default_categories

    strict_sources = _as_bool(
        os.getenv(_profile_env_key(name, "STRICT_SOURCES")),
        default=False,
    )

    settings = {
        "profile": name,
        "categories": categories,
        "time_range": str(
            os.getenv(_profile_env_key(name, "TIME_RANGE"), default_time_range)
        ).strip(),
        "engines": _normalize_text_list(
            os.getenv(_profile_env_key(name, "ENGINES"), default_engines)
        ),
        "allowlist": _normalize_text_list(
            os.getenv(_profile_env_key(name, "ALLOWLIST"), default_allowlist)
        ),
        "blocklist": _normalize_text_list(
            os.getenv(_profile_env_key(name, "BLOCKLIST"), "")
        ),
        "strict_sources": strict_sources,
    }
    return settings


def _extract_query_tokens(query: str) -> list[str]:
    lowered = str(query or "").strip().lower()
    if not lowered:
        return []
    stopwords = {
        "今天",
        "今日",
        "明天",
        "现在",
        "当地",
        "查询",
        "获取",
        "实时",
        "night",
        "day",
        "weather",
    }
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", lowered)
    output: list[str] = []
    for token in tokens:
        normalized = str(token or "").strip()
        if not normalized or normalized in stopwords:
            continue
        if normalized in output:
            continue
        output.append(normalized)
    return output


def _score_result(
    item: dict[str, Any],
    *,
    query_text: str,
    intent_profile: str,
    allowlist: list[str],
) -> int:
    score = 0
    domain = _domain_from_url(str(item.get("url") or ""))
    if _matches_any_rule(domain, allowlist):
        score += 80

    for host, boost in PROFILE_PRIORITY_SCORES.get(intent_profile, {}).items():
        if _domain_matches_rule(domain, host):
            score += int(boost)
    for host, penalty in PROFILE_LOW_PRIORITY_SCORES.get(intent_profile, {}).items():
        if _domain_matches_rule(domain, host):
            score += int(penalty)

    title = str(item.get("title") or "").lower()
    content = str(item.get("content") or "").lower()
    haystack = (title + "\n" + content).strip()
    for token in _extract_query_tokens(query_text)[:8]:
        if token in haystack:
            score += 6
    for keyword in INTENT_KEYWORDS_BY_PROFILE.get(intent_profile, ()):
        if str(keyword or "").lower() in haystack:
            score += 2
    if str(item.get("publishedDate") or "").strip():
        score += 1
    return score


def _rerank_results(
    rows: list[Any],
    *,
    query_text: str,
    intent_profile: str,
    num_results: int,
    allowlist: list[str],
    blocklist: list[str],
    strict_sources: bool,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        domain = _domain_from_url(url)
        if not domain:
            continue
        if _matches_any_rule(domain, blocklist):
            continue

        normalized = dict(item)
        normalized["_domain"] = domain
        normalized["_score"] = _score_result(
            normalized,
            query_text=query_text,
            intent_profile=intent_profile,
            allowlist=allowlist,
        )
        prepared.append(normalized)
        seen_urls.add(url)

    if strict_sources and allowlist:
        strict_rows = [
            row
            for row in prepared
            if _matches_any_rule(str(row.get("_domain") or ""), allowlist)
        ]
        if strict_rows:
            prepared = strict_rows

    prepared.sort(
        key=lambda row: (
            int(row.get("_score") or 0),
            bool(str(row.get("publishedDate") or "").strip()),
            len(str(row.get("content") or "")),
        ),
        reverse=True,
    )
    return prepared[:num_results]


def _build_search_url(
    *,
    search_endpoint: str,
    query_text: str,
    categories: str,
    time_range: str,
    language: str,
    engines: list[str],
) -> str:
    params: dict[str, Any] = {
        "q": str(query_text or "").strip(),
        "format": "json",
    }
    if categories:
        params["categories"] = categories
    if time_range:
        params["time_range"] = time_range
    if language:
        params["language"] = language
    if engines:
        params["engines"] = ",".join(engines)
    return f"{search_endpoint}?{urlencode(params)}"


def _build_weather_site_query(query_text: str, allowlist: list[str]) -> str:
    sites: list[str] = []
    for host in allowlist:
        domain = str(host or "").strip().lower().lstrip(".")
        if not domain:
            continue
        if domain in sites:
            continue
        if domain.endswith(".cn") or domain in WEATHER_PRIORITY_SCORES:
            sites.append(domain)
    if not sites:
        return ""
    clauses = [f"site:{host}" for host in sites[:3]]
    return f"{query_text} ({' OR '.join(clauses)})"


def _normalize_queries(query: str, queries: Any) -> list[str]:
    output: list[str] = []
    seed = _normalize_text_list(queries)
    single = str(query or "").strip()
    if single:
        seed.insert(0, single)
    for item in seed:
        rendered = str(item or "").strip()
        if not rendered or rendered in output:
            continue
        output.append(rendered)
    return output[:MAX_QUERIES]


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> dict:
    _ = (ctx, runtime)
    query = str(params.get("query") or "").strip()
    queries = _normalize_queries(query, params.get("queries"))

    if not queries:
        return {"text": "❌ 请提供搜索关键词", "ui": {}}

    try:
        num_results = min(max(1, int(params.get("num_results", 5))), MAX_RESULTS)
    except Exception:
        num_results = 5

    explicit_profile = _normalize_intent_profile(params.get("intent_profile"))
    detected_profile = _detect_intent_profile(queries)
    intent_profile = explicit_profile or detected_profile
    profile_settings = _load_intent_profile_settings(intent_profile)

    categories = str(params.get("categories") or "").strip()
    time_range = str(params.get("time_range") or "").strip().lower()
    language = str(params.get("language") or "zh-CN").strip()
    engines = _normalize_text_list(params.get("engines"))

    if not categories:
        categories = str(profile_settings.get("categories") or "general").strip()
    if not time_range:
        time_range = str(profile_settings.get("time_range") or "").strip().lower()
    if not engines:
        engines = _normalize_text_list(profile_settings.get("engines"))

    search_endpoint = _normalize_base_url(os.getenv("SEARXNG_URL", ""))
    if not search_endpoint:
        return {
            "text": "⚠️ 搜索服务未配置 (SEARXNG_URL missing). 技能暂时不可用。",
            "ui": {},
        }

    weather_intent = intent_profile == "weather"
    strict_default = bool(profile_settings.get("strict_sources"))
    strict_sources = _as_bool(params.get("strict_sources"), default=strict_default)
    rerank_enabled = _as_bool(
        os.getenv("SEARXNG_SEARCH_RERANK_ENABLED"),
        default=True,
    )

    allowlist = _normalize_text_list(params.get("site_allowlist"))
    allowlist = _merge_unique(
        allowlist,
        _normalize_text_list(os.getenv("SEARXNG_SEARCH_ALLOWLIST", "")),
    )
    allowlist = _merge_unique(
        allowlist, _normalize_text_list(profile_settings.get("allowlist"))
    )
    blocklist = _normalize_text_list(params.get("site_blocklist"))
    blocklist = _merge_unique(
        blocklist,
        _normalize_text_list(os.getenv("SEARXNG_SEARCH_BLOCKLIST_DEFAULT", "")),
    )
    blocklist = _merge_unique(
        blocklist,
        _normalize_text_list(profile_settings.get("blocklist")),
    )

    async def fetch_results(
        client: httpx.AsyncClient,
        search_query: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        async def request_once(
            *,
            query_text: str,
            categories_value: str,
        ) -> list[dict[str, Any]]:
            search_url = _build_search_url(
                search_endpoint=search_endpoint,
                query_text=query_text,
                categories=categories_value,
                time_range=time_range,
                language=language,
                engines=engines,
            )
            response = await client.get(search_url)
            response.raise_for_status()
            data = response.json() if response is not None else {}
            rows = data.get("results") if isinstance(data, dict) else []
            if not isinstance(rows, list):
                rows = []
            if rerank_enabled:
                return _rerank_results(
                    rows,
                    query_text=query_text,
                    intent_profile=intent_profile,
                    num_results=num_results,
                    allowlist=allowlist,
                    blocklist=blocklist,
                    strict_sources=strict_sources,
                )
            return [item for item in rows if isinstance(item, dict)][:num_results]

        try:
            ranked = await request_once(
                query_text=search_query,
                categories_value=categories,
            )

            if not ranked and categories and categories != "general":
                ranked = await request_once(
                    query_text=search_query,
                    categories_value="general",
                )

            if not ranked and weather_intent and "site:" not in search_query.lower():
                weather_query = _build_weather_site_query(search_query, allowlist)
                if weather_query:
                    ranked = await request_once(
                        query_text=weather_query,
                        categories_value="general",
                    )
            return search_query, ranked
        except Exception:
            return search_query, []

    async with httpx.AsyncClient(timeout=30.0) as client:
        results_list = await asyncio.gather(
            *(fetch_results(client, item) for item in queries)
        )

    report_lines: list[str] = ["# 🔍 搜索聚合报告\n"]
    agent_summary_lines: list[str] = []
    found_any = False
    for query_text, res_items in results_list:
        report_lines.append(f"## 搜索: {query_text}\n")
        agent_summary_lines.append(f"## 搜索: {query_text}")

        if not res_items:
            report_lines.append("> 未找到相关结果\n")
            agent_summary_lines.append("> 无结果")
            continue

        found_any = True
        for item in res_items:
            title = str(item.get("title") or "No Title")
            url = str(item.get("url") or "#")
            content = str(item.get("content") or "")
            engine = str(item.get("engine") or "unknown")
            pub_date = str(item.get("publishedDate") or "")
            domain = _domain_from_url(url)

            meta_parts = [f"`{engine}`"]
            if domain:
                meta_parts.append(f"🌐 {domain}")
            if pub_date:
                meta_parts.append(f"🕒 {pub_date}")
            meta_line = " · ".join(meta_parts)

            report_lines.append(f"### [{title}]({url})\n")
            report_lines.append(f"{meta_line}\n")
            if content:
                report_lines.append(f"{content}\n")
            report_lines.append("---\n")

            domain_hint = f" [{domain}]" if domain else ""
            snippet = content[:100] + "..." if len(content) > 100 else content
            agent_summary_lines.append(f"- [{title}]({url}){domain_hint}\n  {snippet}")

    if not found_any:
        return {"text": "No results found for any query.", "ui": {}}

    report_md = "\n".join(report_lines)
    report_bytes = report_md.encode("utf-8")
    final_text = "🔇🔇🔇【搜索结果摘要】\n\n" + "\n".join(agent_summary_lines)
    return {
        "text": final_text,
        "files": {"search_report.md": report_bytes},
        "ui": {},
    }
