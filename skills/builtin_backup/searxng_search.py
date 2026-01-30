"""
SearXNG 网络搜索 Skill - 通过本地部署的 SearXNG 进行网络搜索
"""
from urllib.parse import quote
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text


SKILL_META = {
    "name": "searxng_search",
    "description": "通过本地 SearXNG 实例进行网络搜索，支持分类、时间范围筛选。",
    "triggers": ["搜索", "search", "查询", "谷歌", "百度", "bing"],
    "params": {
        "query": {
            "type": "str",
            "description": "搜索关键词",
            "required": True
        },
        "num_results": {
            "type": "int",
            "description": "返回结果数量 (1-10)",
            "default": 5
        },
        "categories": {
            "type": "str",
            "description": "搜索分类: general, news, it, science, files, images, videos, social media, map",
            "default": "general"
        },
        "time_range": {
            "type": "str",
            "description": "时间范围: day, week, month, year",
            "default": ""
        },
        "language": {
            "type": "str",
            "description": "搜索语言 (如 zh-CN, en-US)",
            "default": "zh-CN"
        }
    },
    "version": "1.1.0",
    "author": "257675041"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    query = params.get("query", "").strip()
    num_results = params.get("num_results", 5)
    categories = params.get("categories", "general")
    time_range = params.get("time_range", "")
    language = params.get("language", "zh-CN")
    
    if not query:
        await smart_reply_text(update, "❌ 请提供搜索关键词")
        return
    
    # 限制结果数量
    num_results = min(max(1, int(num_results)), 10)
    
    # 构建提示信息
    status_parts = [f"🔍 正在搜索: {query}"]
    if categories != "general":
        status_parts.append(f"📂 分类: {categories}")
    if time_range:
        status_parts.append(f"🕒 时间: {time_range}")
    
    await smart_reply_text(update, " | ".join(status_parts))
    
    try:
        # 构建 SearXNG API 请求 URL
        # 参数文档: https://docs.searxng.org/dev/search_api.html
        encoded_query = quote(query)
        
        # Base URL
        search_url = f"http://192.168.1.100:28080/search?q={encoded_query}&format=json"
        
        # Add optional params
        if categories:
            search_url += f"&categories={categories}"
        if time_range:
            search_url += f"&time_range={time_range}"
        if language:
            search_url += f"&language={language}"
            
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            data = response.json()
        
        results = data.get("results", [])[:num_results]
        
        if not results:
            msg = f"😔 未找到与 「{query}」 相关的结果"
            await smart_reply_text(update, msg)
            return msg # Return for Agent
        
        # 格式化搜索结果
        message_lines = [f"🔎 搜索结果: {query}\n"]
        
        for i, result in enumerate(results, 1):
            title = result.get("title", "无标题")
            url = result.get("url", "")
            content = result.get("content", "")
            ws_engine = result.get("engine", "") # Source engine, e.g. google, bing
            published_date = result.get("publishedDate", "")
            
            # 截断过长的内容
            if len(content) > 150:
                content = content[:150] + "..."
            
            # 构建标题行 (含来源)
            source_tag = f"[{ws_engine}] " if ws_engine else ""
            message_lines.append(f"{i}. {source_tag}<b>{title}</b>")
            
            if published_date:
                # 尝试简单格式化日期 (SearXNG date output might handle formatting)
                message_lines.append(f"   🕒 {published_date}")
                
            if content:
                message_lines.append(f"   {content}")
            message_lines.append(f"   🔗 {url}\n")
        
        result_message = "\n".join(message_lines)
        await smart_reply_text(update, result_message, parse_mode="HTML")
        return result_message # RETURN RESULT TO AGENT
        
    except httpx.TimeoutException:
        msg = "❌ 搜索请求超时，请稍后重试"
        await smart_reply_text(update, msg)
        return msg
    except httpx.HTTPStatusError as e:
        msg = f"❌ 搜索服务返回错误: {e.response.status_code}"
        await smart_reply_text(update, msg)
        return msg
    except Exception as e:
        msg = f"❌ 搜索失败: {str(e)}"
        await smart_reply_text(update, msg)
        return msg