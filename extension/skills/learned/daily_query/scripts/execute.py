from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import httpx
from core.platform.models import UnifiedContext
from core.skill_menu import make_callback, parse_callback
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)

logger = logging.getLogger(__name__)
DAILY_MENU_NS = "dlym"

WEATHER_CODE_MAP = {
    0: "晴",
    1: "基本晴",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "冻雾",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "强雷暴伴冰雹",
}


async def _fetch_aqi(lat: float, lon: float) -> str:
    url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=european_aqi,pm10,pm2_5"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                current = data.get("current", {})
                aqi = current.get("european_aqi")
                pm25 = current.get("pm2_5")
                pm10 = current.get("pm10")
                if aqi is not None:
                    level = (
                        "优"
                        if aqi <= 20
                        else "良"
                        if aqi <= 40
                        else "中度"
                        if aqi <= 60
                        else "差"
                        if aqi <= 80
                        else "极差"
                    )
                    return f"空气质量指数(EAQI): **{aqi}** ({level}) | PM2.5: {pm25} μg/m³ | PM10: {pm10} μg/m³"
    except Exception as e:
        logger.warning(f"[daily_query] AQI fetch failed: {e}")
    return ""


def _weather_code_text(code: Any) -> str:
    try:
        numeric = int(code)
    except Exception:
        return "未知"
    return WEATHER_CODE_MAP.get(numeric, f"天气代码 {numeric}")


async def _fetch_weather_open_meteo(location: str) -> dict:
    target = str(location or "").strip()
    if not target:
        return {"text": "❌ 未提供地点，无法回退到备用天气源。", "ui": {}}

    geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
    
    # 尝试多个搜索关键词，提高命中率
    search_terms = [target]
    # 如果包含"区"，尝试去掉"区"再搜一次（如"滨湖区" -> "滨湖"，"无锡市滨湖区" -> "无锡市滨湖"）
    if "区" in target:
        search_terms.append(target.replace("区", ""))
    # 如果包含"市"，尝试只保留市名（如"无锡市滨湖区" -> "无锡市"）
    if "市" in target:
        city_part = target.split("市")[0] + "市"
        if city_part not in search_terms:
            search_terms.append(city_part)
    # 最后尝试只搜第一个词（如"无锡市滨湖区" -> "无锡市"）
    first_word = target.split()[0] if " " in target else target.split("市")[0] + "市" if "市" in target else target
    if first_word not in search_terms:
        search_terms.append(first_word)
    
    # 去重
    search_terms = list(dict.fromkeys(search_terms))
    
    place = None
    lat = lon = admin1 = country = None
    display_name = target
    
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        for term in search_terms:
            geo_resp = await client.get(
                geocode_url,
                params={
                    "name": term,
                    "count": 1,
                    "language": "zh",
                    "format": "json",
                },
            )
            if geo_resp.status_code == 200:
                geo_data = geo_resp.json() if geo_resp is not None else {}
                results = geo_data.get("results") if isinstance(geo_data, dict) else []
                if isinstance(results, list) and results:
                    place = dict(results[0] or {})
                    lat = float(place.get("latitude"))
                    lon = float(place.get("longitude"))
                    display_name = str(place.get("name") or term).strip()
                    admin1 = str(place.get("admin1") or "").strip()
                    country = str(place.get("country") or "").strip()
                    logger.info(f"[daily_query] Open-Meteo geocoding success with term: '{term}' -> {display_name}")
                    break
                else:
                    logger.debug(f"[daily_query] Open-Meteo geocoding failed for term: '{term}'")
        
        if not place:
            return {
                "text": f"❌ 备用天气源未找到地点：{target} (尝试了: {', '.join(search_terms)})",
                "ui": {},
            }

        weather_resp = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "timezone": "auto",
                "forecast_days": 3,
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,wind_speed_10m_max"
                ),
            },
        )
        weather_resp.raise_for_status()
        weather_data = weather_resp.json() if weather_resp is not None else {}

    current = weather_data.get("current") if isinstance(weather_data, dict) else {}
    daily = weather_data.get("daily") if isinstance(weather_data, dict) else {}
    dates = list(daily.get("time") or [])
    max_temps = list(daily.get("temperature_2m_max") or [])
    min_temps = list(daily.get("temperature_2m_min") or [])
    weather_codes = list(daily.get("weather_code") or [])
    rain_probs = list(daily.get("precipitation_probability_max") or [])
    max_winds = list(daily.get("wind_speed_10m_max") or [])

    place_parts = [item for item in [country, admin1, display_name] if item]
    title = " ".join(place_parts) if place_parts else display_name

    lines = [f"✅ {title} 的天气预报如下（备用天气源 Open-Meteo）：", ""]
    if isinstance(current, dict) and current:
        current_temp = current.get("temperature_2m")
        current_code = current.get("weather_code")
        current_wind = current.get("wind_speed_10m")
        lines.append(
            "当前："
            f"{current_temp}°C，{_weather_code_text(current_code)}，"
            f"风速 {current_wind} km/h"
        )
        lines.append("")

    labels = ["今天", "明天", "后天"]
    for idx, date_value in enumerate(dates[:3]):
        label = labels[idx] if idx < len(labels) else f"第 {idx + 1} 天"
        lines.append(
            f"{label}（{date_value}）："
            f"{_weather_code_text(weather_codes[idx] if idx < len(weather_codes) else '')}，"
            f"{min_temps[idx] if idx < len(min_temps) else '?'} ~ "
            f"{max_temps[idx] if idx < len(max_temps) else '?'}°C，"
            f"降水概率 {rain_probs[idx] if idx < len(rain_probs) else '?'}%，"
            f"最大风速 {max_winds[idx] if idx < len(max_winds) else '?'} km/h"
        )

    aqi_info = await _fetch_aqi(lat, lon)
    if aqi_info:
        lines.extend(["", f"空气质量：{aqi_info}"])

    return {"text": "\n".join(lines).strip(), "ui": {}}


async def _fetch_weather(location: str) -> dict:
    target = location.strip() if location else ""
    # URL 编码地址，防止“无锡 滨湖区”这种带空格的地点引发 httpx 异常
    encoded_target = urllib.parse.quote(target) if target else ""
    # "lang=zh" for Chinese localization, "T" to strip ANSI terminal colors.
    url = f"https://wttr.in/{encoded_target}?lang=zh&T"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "curl/7.68.0"})
            if resp.status_code == 200 and resp.text.strip():
                text = resp.text

                aqi_info = ""
                coords_match = re.search(r"\[([-\d\.]+),\s*([-\d\.]+)\]", text)
                if coords_match:
                    try:
                        lat, lon = (
                            float(coords_match.group(1)),
                            float(coords_match.group(2)),
                        )
                        aqi_info = await _fetch_aqi(lat, lon)
                    except Exception:
                        pass

                title_target_name = target or "当前所在位置"
                if aqi_info:
                    msg = f"✅ {title_target_name} 的天气预报及空气质量如下：\n\n**🌍 【实时空气质量】**\n{aqi_info}\n\n**🌤️ 【天气预报】**\n```text\n{text}\n```\n"
                else:
                    msg = f"✅ {title_target_name} 的天气预报如下：\n\n```text\n{text}\n```\n"

                return {"text": msg, "ui": {}}
            logger.warning(
                "[daily_query] wttr.in returned status=%s for location=%r; fallback to Open-Meteo",
                resp.status_code,
                target,
            )
            if target:
                return await _fetch_weather_open_meteo(target)
            return {
                "text": f"❌ 天气获取失败，HTTP 状态码: {resp.status_code}",
                "ui": {},
            }
    except Exception as e:
        logger.error(f"[daily_query] weather fetch failed: {repr(e)}")
        if target:
            try:
                return await _fetch_weather_open_meteo(target)
            except Exception as fallback_exc:
                logger.error(
                    "[daily_query] open-meteo fallback failed: %r",
                    fallback_exc,
                )
        return {"text": "❌ 获取天气时发生网络或解析错误，请稍后重试。", "ui": {}}


async def _fetch_crypto(symbol: str) -> dict:
    target = (symbol.strip() or "BTC").upper()
    if not target.endswith("USDT"):
        target = f"{target}USDT"
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={target}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                price = float(data.get("price", 0))
                return {
                    "text": f"✅ {target} 当前价格为: **${price:,.2f}**\n数据来源: Binance",
                    "ui": {},
                }
            return {
                "text": f"❌ 获取 {target} 价格失败，请检查代码是否正确（如 BTC, ETH 等）。",
                "ui": {},
            }
    except Exception as e:
        return {"text": f"❌ 获取加密货币价格时发生错误: {e}", "ui": {}}


async def _fetch_currency(symbol: str) -> dict:
    target = (symbol.strip() or "USD").upper()
    url = f"https://api.exchangerate-api.com/v4/latest/{target}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                rates = data.get("rates", {})
                popular = {
                    "USD": rates.get("USD"),
                    "CNY": rates.get("CNY"),
                    "EUR": rates.get("EUR"),
                    "JPY": rates.get("JPY"),
                    "GBP": rates.get("GBP"),
                    "AUD": rates.get("AUD"),
                    "CAD": rates.get("CAD"),
                    "HKD": rates.get("HKD"),
                }
                lines = [f"✅ **{target}** 当前主要汇率如下 (ExchangeRate-API):"]
                for cur, rate in popular.items():
                    if rate and cur != target:
                        lines.append(f"- 1 {target} = {rate} {cur}")
                return {"text": "\n".join(lines), "ui": {}}
            return {
                "text": f"❌ 获取 {target} 汇率失败，请检查法币代码是否正确（如 USD, CNY）。",
                "ui": {},
            }
    except Exception as e:
        return {"text": f"❌ 获取汇率时发生错误: {e}", "ui": {}}


async def execute(ctx: UnifiedContext, params: dict, runtime=None):
    query_type = str(params.get("query_type", "weather")).strip().lower()
    location = str(params.get("location", "")).strip()
    symbol = str(params.get("symbol", "")).strip()

    logger.info(f"[daily_query] type={query_type} location={location} symbol={symbol}")
    if query_type == "weather":
        yield f"🌦️ 正在查询 {location or '当地'} 的天气预报..."
        result = await _fetch_weather(location)
        yield result
        return

    if query_type == "crypto":
        yield f"📈 正在查询 {symbol or 'BTC'} 的最新价格..."
        result = await _fetch_crypto(symbol)
        yield result
        return

    if query_type == "currency":
        yield f"💱 正在查询 {symbol or 'USD'} 的最新汇率..."
        result = await _fetch_currency(symbol)
        yield result
        return

    if query_type == "time":
        now = datetime.now()
        yield {
            "text": f"🕒 当前服务器系统时间为: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "ui": {},
        }
        return

    yield {"text": f"❌ 不支持的日常查询分类: {query_type}", "ui": {}}


def _parse_daily_subcommand(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "menu", ""

    parts = raw.split(maxsplit=2)
    if not parts or not parts[0].startswith("/daily"):
        return "help", ""
    if len(parts) == 1:
        return "menu", ""

    sub = str(parts[1] or "").strip().lower()
    args = str(parts[2] if len(parts) >= 3 else "").strip()

    if sub in {"menu", "home", "start"}:
        return "menu", ""
    if sub in {"help", "h", "?"}:
        return "help", ""
    if sub in {"weather", "time", "crypto", "currency"}:
        return sub, args
    return "help", ""


def _daily_usage_text() -> str:
    return (
        "用法:\n"
        "`/daily`\n"
        "`/daily weather [地点]`\n"
        "`/daily crypto [币种]`\n"
        "`/daily currency [法币]`\n"
        "`/daily time`"
    )


def _daily_menu_ui() -> dict:
    return {
        "actions": [
            [
                {"text": "🕒 当前时间", "callback_data": make_callback(DAILY_MENU_NS, "time")},
                {"text": "₿ BTC 价格", "callback_data": make_callback(DAILY_MENU_NS, "crypto", "BTC")},
            ],
            [
                {"text": "💱 USD 汇率", "callback_data": make_callback(DAILY_MENU_NS, "currency", "USD")},
                {"text": "🌦️ 天气用法", "callback_data": make_callback(DAILY_MENU_NS, "weatherhelp")},
            ],
        ]
    }


def _daily_weather_help_response() -> dict:
    return {
        "text": (
            "🌦️ **天气查询**\n\n"
            "直接发送：\n"
            "• `/daily weather 无锡`\n"
            "• `/daily weather Tokyo`\n"
            "• `/daily weather`（使用服务器所在地）"
        ),
        "ui": {
            "actions": [
                [
                    {"text": "🏠 返回首页", "callback_data": make_callback(DAILY_MENU_NS, "home")},
                    {"text": "🕒 当前时间", "callback_data": make_callback(DAILY_MENU_NS, "time")},
                ]
            ]
        },
    }


async def _run_daily_query(ctx: UnifiedContext, params: dict) -> dict:
    final_payload: dict | None = None
    async for chunk in execute(ctx, params):
        if isinstance(chunk, dict) and ("text" in chunk or "ui" in chunk):
            final_payload = dict(chunk)
    return final_payload or {"text": "❌ 查询失败。", "ui": {}}


async def show_daily_menu() -> dict:
    return {
        "text": (
            "🧭 **日常查询**\n\n"
            "支持天气、时间、加密货币价格和法币汇率。\n"
            "你也可以直接输入 `/daily weather 无锡` 这类命令。"
        ),
        "ui": _daily_menu_ui(),
    }


async def handle_daily_menu_callback(ctx: UnifiedContext):
    data = ctx.callback_data
    if not data:
        return

    action, parts = parse_callback(data, DAILY_MENU_NS)
    if not action:
        return

    await ctx.answer_callback()
    if action == "home":
        payload = await show_daily_menu()
    elif action == "time":
        payload = await _run_daily_query(ctx, {"query_type": "time"})
        payload["ui"] = _daily_menu_ui()
    elif action == "crypto":
        payload = await _run_daily_query(
            ctx,
            {
                "query_type": "crypto",
                "symbol": str(parts[0] if parts else "BTC").strip() or "BTC",
            },
        )
        payload["ui"] = _daily_menu_ui()
    elif action == "currency":
        payload = await _run_daily_query(
            ctx,
            {
                "query_type": "currency",
                "symbol": str(parts[0] if parts else "USD").strip() or "USD",
            },
        )
        payload["ui"] = _daily_menu_ui()
    elif action == "weatherhelp":
        payload = _daily_weather_help_response()
    else:
        payload = {"text": "❌ 未知操作。", "ui": _daily_menu_ui()}

    await ctx.edit_message(ctx.message.id, payload["text"], ui=payload.get("ui"))


def register_handlers(adapter_manager):
    from core.config import is_user_allowed

    async def cmd_daily(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return

        action, args = _parse_daily_subcommand(ctx.message.text or "")
        if action == "menu":
            return await show_daily_menu()
        if action == "time":
            payload = await _run_daily_query(ctx, {"query_type": "time"})
            payload["ui"] = _daily_menu_ui()
            return payload
        if action == "crypto":
            payload = await _run_daily_query(
                ctx,
                {"query_type": "crypto", "symbol": args or "BTC"},
            )
            payload["ui"] = _daily_menu_ui()
            return payload
        if action == "currency":
            payload = await _run_daily_query(
                ctx,
                {"query_type": "currency", "symbol": args or "USD"},
            )
            payload["ui"] = _daily_menu_ui()
            return payload
        if action == "weather":
            payload = await _run_daily_query(
                ctx,
                {"query_type": "weather", "location": args},
            )
            payload["ui"] = _daily_menu_ui()
            return payload
        return {"text": _daily_usage_text(), "ui": _daily_menu_ui()}

    adapter_manager.on_command("daily", cmd_daily, description="天气时间汇率查询")
    adapter_manager.on_callback_query("^dlym_", handle_daily_menu_callback)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Daily query skill CLI bridge.",
    )
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    weather_parser = subparsers.add_parser("weather", help="Query weather")
    weather_parser.add_argument("location", nargs="?", default="", help="Location")

    crypto_parser = subparsers.add_parser("crypto", help="Query crypto price")
    crypto_parser.add_argument("symbol", nargs="?", default="BTC", help="Symbol")

    currency_parser = subparsers.add_parser("currency", help="Query FX rates")
    currency_parser.add_argument("symbol", nargs="?", default="USD", help="Base currency")

    subparsers.add_parser("time", help="Show server time")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "").strip().lower()
    if command == "weather":
        return merge_params(
            args,
            {"query_type": "weather", "location": str(args.location or "").strip()},
        )
    if command == "crypto":
        return merge_params(
            args,
            {"query_type": "crypto", "symbol": str(args.symbol or "BTC").strip()},
        )
    if command == "currency":
        return merge_params(
            args,
            {"query_type": "currency", "symbol": str(args.symbol or "USD").strip()},
        )
    if command == "time":
        return merge_params(args, {"query_type": "time"})
    raise SystemExit(f"unsupported command: {command}")


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


from core.extension_base import SkillExtension


class DailyQuerySkillExtension(SkillExtension):
    name = "daily_query_extension"
    skill_name = "daily_query"

    def register(self, runtime) -> None:
        register_handlers(runtime.adapter_manager)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
