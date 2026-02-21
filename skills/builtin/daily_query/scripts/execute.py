import logging
import httpx
import re
from datetime import datetime
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)


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


async def _fetch_weather(location: str) -> dict:
    target = location.strip() if location else ""
    # "lang=zh" for Chinese localization, "T" to strip ANSI terminal colors.
    url = f"https://wttr.in/{target}?lang=zh&T"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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
            return {
                "text": f"❌ 天气获取失败，HTTP 状态码: {resp.status_code}",
                "ui": {},
            }
    except Exception as e:
        logger.error(f"[daily_query] weather fetch failed: {e}")
        return {"text": f"❌ 获取天气时发生错误: {e}", "ui": {}}


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
