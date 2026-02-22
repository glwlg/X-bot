import re
import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from core.platform.models import UnifiedContext

# 优先 requests + BeautifulSoup；若不可用则退回标准库
try:
    import requests  # type: ignore
except Exception:
    requests = None

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None

from html.parser import HTMLParser
from urllib.request import Request, urlopen


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str):
        if data and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


def _detect_query_type(query: str) -> str:
    q = (query or "").strip()
    if re.fullmatch(r"\d{6}", q):
        return "zipcode"
    return "address"


def _http_get(url: str, timeout: int = 10) -> str:
    if requests is not None:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text

    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def _normalize_region(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" -|，,；;：:")
    return s


def _extract_plain_text(html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text("\n", strip=True)
    p = _TextExtractor()
    p.feed(html)
    return p.text()


def _parse_candidates_from_html(html: str) -> List[Dict[str, str]]:
    candidates: List[Dict[str, str]] = []

    # 方案1：结构化表格解析（BS4）
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if not cells:
                continue
            line = " | ".join(cells)
            zips = re.findall(r"\b(\d{6})\b", line)
            if not zips:
                continue
            for z in zips:
                region_parts = [c for c in cells if z not in c]
                region = _normalize_region(" ".join(region_parts))
                if not region:
                    region = _normalize_region(re.sub(r"\b\d{6}\b", "", line))
                if region:
                    candidates.append({"region": region, "zipcode": z})

    # 方案2：全文正则兜底
    text = _extract_plain_text(html)
    text = re.sub(r"[\t\r]+", " ", text)

    # 地区在前
    for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9·\-\s]{2,50})\s*(\d{6})", text):
        region = _normalize_region(m.group(1))
        zipcode = m.group(2)
        if region and not re.fullmatch(r"\d{6}", region):
            candidates.append({"region": region, "zipcode": zipcode})

    # 邮编在前
    for m in re.finditer(r"(\d{6})\s*([\u4e00-\u9fa5A-Za-z0-9·\-\s]{2,50})", text):
        zipcode = m.group(1)
        region = _normalize_region(m.group(2))
        if region and not re.fullmatch(r"\d{6}", region):
            candidates.append({"region": region, "zipcode": zipcode})

    # 去重与过滤噪声
    cleaned: List[Dict[str, str]] = []
    seen = set()
    for c in candidates:
        r = c.get("region", "")
        z = c.get("zipcode", "")
        if not z or not re.fullmatch(r"\d{6}", z):
            continue
        if len(r) < 2:
            continue
        # 降噪：过长文本截断
        if len(r) > 60:
            r = r[:60].strip()
        key = (r, z)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"region": r, "zipcode": z})

    return cleaned


def _rank_candidates(query: str, candidates: List[Dict[str, str]], query_type: str) -> List[Dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return candidates

    def score(c: Dict[str, str]) -> int:
        s = 0
        region = c.get("region", "")
        z = c.get("zipcode", "")
        if query_type == "zipcode":
            if z == q:
                s += 10
            if q in region:
                s += 2
        else:
            # 地址查询：区域命中越多分越高
            for token in re.split(r"\s+", q):
                token = token.strip()
                if token and token in region:
                    s += 2
            if q in region:
                s += 5
        return s

    return sorted(candidates, key=score, reverse=True)


def _lookup_youbianku(query: str, query_type: str) -> List[Dict[str, str]]:
    # 使用公开检索页
    encoded = quote(query)
    urls = []
    if query_type == "zipcode":
        urls = [
            f"https://www.youbianku.com/Search?zipcode={encoded}",
            f"https://www.youbianku.com/Search?q={encoded}",
        ]
    else:
        urls = [
            f"https://www.youbianku.com/Search?address={encoded}",
            f"https://www.youbianku.com/Search?q={encoded}",
        ]

    all_candidates: List[Dict[str, str]] = []
    for u in urls:
        html = _http_get(u, timeout=12)
        all_candidates.extend(_parse_candidates_from_html(html))

    return _rank_candidates(query, all_candidates, query_type)


def _format_output(query: str, candidates: List[Dict[str, str]], source: str) -> str:
    lines = [f"查询内容：{query}"]
    if not candidates:
        lines.append("结果：未检索到明确匹配。")
        lines.append("建议：请补充更完整地址（至少包含省/市/区），或确认邮编为6位数字。")
        lines.append(f"数据来源：{source}")
        return "\n".join(lines)

    lines.append("结果列表：")
    for i, c in enumerate(candidates, 1):
        lines.append(f"{i}. 地区：{c.get('region', '未知')} | 邮编：{c.get('zipcode', '未知')}")
    lines.append(f"数据来源：{source}")
    return "\n".join(lines)


async def execute(ctx: UnifiedContext, params: dict):
    yield "正在解析查询参数..."

    action = (params or {}).get("action", "lookup")
    query = (params or {}).get("query") or (params or {}).get("text") or ""
    max_results = (params or {}).get("max_results", 3)

    try:
        max_results = int(max_results)
    except Exception:
        max_results = 3
    max_results = max(1, min(3, max_results))

    if action != "lookup":
        yield {
            "text": "🔇🔇🔇仅支持 action=lookup。",
            "ui": {"actions": [[{"text": "示例：查邮编 无锡滨湖区", "callback_data": "postal_example"}]]},
        }
        return

    query = str(query).strip()
    if not query:
        yield {
            "text": "🔇🔇🔇请输入要查询的地址或6位邮编，例如：查邮编：北京海淀区中关村。",
            "ui": {"actions": [[{"text": "示例：100080 是哪里", "callback_data": "postal_example2"}]]},
        }
        return

    query_type = _detect_query_type(query)
    yield "正在访问公开网页并解析结果..."

    try:
        candidates = await asyncio.to_thread(_lookup_youbianku, query, query_type)
    except Exception as e:
        msg = (
            f"查询内容：{query}\n"
            f"结果：查询失败（网络异常或目标站点暂不可用）。\n"
            f"建议：稍后重试，或补充更完整地址（省/市/区）再查询。\n"
            f"数据来源：邮编库公开页面（youbianku.com）\n"
            f"错误信息：{str(e)[:120]}"
        )
        yield {
            "text": f"🔇🔇🔇{msg}",
            "ui": {"actions": [[{"text": "重试查询", "callback_data": "postal_retry"}]]},
        }
        return

    top = candidates[:max_results]
    text = _format_output(query, top, "邮编库公开页面（youbianku.com）")

    yield {
        "text": f"🔇🔇🔇{text}",
        "ui": {
            "actions": [
                [{"text": "再查一个", "callback_data": "postal_lookup_again"}],
                [{"text": "示例：邮政编码 北京 海淀 中关村", "callback_data": "postal_example3"}],
            ]
        },
    }
    return


def register_handlers(adapter_manager: Any):
    # 当前技能仅使用文本触发，不注册额外命令
    pass
