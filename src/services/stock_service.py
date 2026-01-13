"""
股票行情服务 - 封装新浪财经 API
"""
import logging
import re
import httpx

logger = logging.getLogger(__name__)

SINA_QUOTE_URL = "http://hq.sinajs.cn/list="
SINA_SEARCH_URL = "https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key="
HEADERS = {"Referer": "https://finance.sina.com.cn/"}


async def fetch_stock_quotes(stock_codes: list[str]) -> list[dict]:
    """
    批量获取股票实时行情
    
    Args:
        stock_codes: 股票代码列表，如 ["sh601006", "sz000001"]
    
    Returns:
        [{"code": "sh601006", "name": "大秦铁路", "price": 7.88, 
          "change": 0.12, "percent": 1.55, "open": 7.80, "high": 7.90, "low": 7.75}, ...]
    """
    if not stock_codes:
        return []
    
    results = []
    codes_str = ",".join(stock_codes)
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{SINA_QUOTE_URL}{codes_str}",
                headers=HEADERS
            )
            response.raise_for_status()
            
            # 处理 GBK 编码
            content = response.content.decode("gbk", errors="ignore")
            
            # 解析每一行
            for line in content.strip().split("\n"):
                if not line or "=" not in line:
                    continue
                    
                # 提取股票代码: var hq_str_sh601006="..."
                match = re.match(r'var hq_str_(\w+)="(.*)";?', line)
                if not match:
                    continue
                    
                code = match.group(1)
                data = match.group(2)
                
                if not data:
                    continue
                    
                parts = data.split(",")
                if len(parts) < 32:
                    continue
                
                try:
                    name = parts[0]
                    open_price = float(parts[1]) if parts[1] else 0
                    yesterday_close = float(parts[2]) if parts[2] else 0
                    current_price = float(parts[3]) if parts[3] else 0
                    high = float(parts[4]) if parts[4] else 0
                    low = float(parts[5]) if parts[5] else 0
                    
                    change = current_price - yesterday_close
                    percent = (change / yesterday_close * 100) if yesterday_close else 0
                    
                    results.append({
                        "code": code,
                        "name": name,
                        "price": current_price,
                        "change": round(change, 2),
                        "percent": round(percent, 2),
                        "open": open_price,
                        "high": high,
                        "low": low,
                        "yesterday_close": yesterday_close,
                    })
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse stock data for {code}: {e}")
                    continue
                    
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching stock quotes: {e}")
    except Exception as e:
        logger.error(f"Error fetching stock quotes: {e}")
    
    return results


async def search_stock_by_name(keyword: str) -> list[dict]:
    """
    根据名称或代码模糊搜索股票
    
    Args:
        keyword: 搜索关键词，如 "仙鹤" 或 "603733"
    
    Returns:
        [{"code": "sh603733", "name": "仙鹤股份", "market": "沪A"}, ...]
    
    新浪API返回格式: "名称,市场类型,纯代码,完整代码,名称,..."
    例如: "仙鹤股份,11,603733,sh603733,仙鹤股份,,仙鹤股份,99,1,,,"
    """
    if not keyword:
        return []
    
    results = []
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{SINA_SEARCH_URL}{keyword}",
                headers=HEADERS
            )
            response.raise_for_status()
            
            content = response.content.decode("gbk", errors="ignore")
            
            # 格式: var suggestvalue="名称,市场类型,纯代码,完整代码,名称,...;..."
            match = re.search(r'var suggestvalue="(.*)";?', content)
            if not match:
                return []
            
            data = match.group(1)
            if not data:
                return []
            
            for item in data.split(";"):
                parts = item.split(",")
                if len(parts) < 4:
                    continue
                
                # parts[0] = 名称, parts[1] = 市场类型, parts[2] = 纯代码, parts[3] = 完整代码
                stock_name = parts[0]
                market_type = parts[1]
                full_code = parts[3]  # 使用 parts[3] 获取完整代码如 sh603733
                
                # 只保留 A 股（11=沪A, 12=深A）
                if market_type not in ("11", "12"):
                    continue
                
                market_name = "沪A" if market_type == "11" else "深A"
                
                results.append({
                    "code": full_code,
                    "name": stock_name,
                    "market": market_name,
                })
                
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error searching stock: {e}")
    except Exception as e:
        logger.error(f"Error searching stock: {e}")
    
    return results


def format_stock_message(stocks: list[dict]) -> str:
    """
    格式化股票行情消息
    
    Args:
        stocks: fetch_stock_quotes 返回的股票列表
    
    Returns:
        格式化的消息文本
    """
    if not stocks:
        return "暂无股票数据"
    
    lines = ["📈 **自选股行情**\n"]
    
    for stock in stocks:
        # 涨跌符号和颜色提示
        if stock["change"] > 0:
            emoji = "🔴"
            sign = "+"
        elif stock["change"] < 0:
            emoji = "🟢"
            sign = ""
        else:
            emoji = "⚪"
            sign = ""
        
        lines.append(
            f"{emoji} **{stock['name']}** ({stock['code']})\n"
            f"   现价: {stock['price']:.2f}  "
            f"{sign}{stock['change']:.2f} ({sign}{stock['percent']:.2f}%)"
        )
    
    return "\n".join(lines)
