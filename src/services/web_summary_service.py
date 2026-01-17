"""
网页摘要模块 - 提取网页内容并使用 AI 生成摘要
"""
import re
import json
import logging
import asyncio
import os
import httpx
from bs4 import BeautifulSoup

from core.config import gemini_client, GEMINI_MODEL, COOKIES_FILE

logger = logging.getLogger(__name__)

# URL 正则表达式
URL_PATTERN = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+'
)


def extract_urls(text: str) -> list[str]:
    """从文本中提取 URL"""
    return URL_PATTERN.findall(text)


# 视频平台域名检测
VIDEO_DOMAINS = [
    "youtube.com", "youtu.be",
    "twitter.com", "x.com",
    "instagram.com",
    "tiktok.com",
    "bilibili.com"
]

def is_video_platform(url: str) -> bool:
    """检查是否为支持的视频平台 URL"""
    return any(domain in url for domain in VIDEO_DOMAINS)


async def fetch_video_metadata(url: str) -> str | None:
    """使用 yt-dlp 获取视频/帖子元数据"""
    try:
        # 检查 cookies 文件
        cookies_arg = []
        if os.path.exists(COOKIES_FILE):
             cookies_arg = ["--cookies", COOKIES_FILE]

        # 使用 yt-dlp 获取 JSON 元数据 (不下载)
        command = [
            "yt-dlp",
            "--dump-json",
            "--skip-download",
            "--no-warnings",
            "--no-playlist",
        ] + cookies_arg + [
            # 为了防止被 X/Twitter 限制，尝试使用 cookies-from-browser 或者简单的 UA 伪装
            # 这里暂时只依赖 yt-dlp 内置的反爬能力
            url
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            logger.warning(f"yt-dlp metadata fetch failed for {url}: {stderr.decode()}")
            return None
            
        data = json.loads(stdout.decode())
        
        title = data.get("title", "")
        description = data.get("description", "")
        uploader = data.get("uploader", "")
        
        # 针对 X/Twitter 特别处理：description 通常就是推文内容
        content = f"平台：{data.get('extractor_key', 'Unknown')}\n"
        content += f"发布者：{uploader}\n"
        content += f"标题/内容：{title}\n"
        if description and description != title:
            content += f"详细描述：\n{description}\n"
            
        return content
        
    except Exception as e:
        logger.error(f"Error fetching video metadata: {e}")
        return None


async def fetch_fina_news(url: str) -> str | None:
    """Special handler for fina.ifnet.top API"""
    try:
        api_url = "https://fina.ifnet.top/api/news"
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "priority": "u=1, i",
            "referer": "https://fina.ifnet.top/"
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Although the user gave a curl command which usually implies GET, 
            # API endpoints like /api/news could be GET or POST. 
            # curl defaults to GET unless -d is used. The user provided `curl 'url' ...` which is GET.
            # But query params? The user didn't provide any in the curl snippet.
            # Let's assume it fetches latest news.
            
            # 修正：用户提供的 URL 是 https://fina.ifnet.top/api/news
            # 但传入 fetch_webpage_content 的 url 可能是 https://fina.ifnet.top/ (首页)
            # 所以我们应该无视传入的具体 path，直接请求 API 获取最新资讯？
            # 或者如果用户给的是具体文章页，我们需要解析 ID？
            # 简单起见，如果 domain 匹配，直接抓取 API 的 Top News 作为 "当前网页内容"
            
            response = await client.get(api_url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            # 假设返回的是列表或包含列表的字典
            # 需要根据实际结构解析。这里做通用处理。
            
            # 这种 API 通常返回 JSON 列表
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("data", []) or data.get("list", []) or data.get("items", []) or [data]
                
            if not items:
                return f"API 返回了空数据: {data}"
            
            # 构建简化版 JSON 列表，节省 Token
            simplified_items = []
            for item in items[:25]: # 限制前 25 条
                simplified_items.append({
                    "id": item.get("id"),
                    "title": item.get("title") or "No Title",
                    "content": item.get("content", ""),
                    "time": item.get("time") or item.get("created_at"),
                    "source": item.get("source")
                })
            
            # 返回 JSON 字符串，供 AI 进行灵活处理
            return json.dumps(simplified_items, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"Error fetching fina news: {e}")
        return None


# 域名特定处理器注册表
DOMAIN_HANDLERS = {
    "fina.ifnet.top": fetch_fina_news
}


async def fetch_with_browser_snapshot(url: str) -> str | None:
    """
    使用 MCP Playwright 获取网页快照（回退机制）
    
    用于处理静态抓取失败或需要 JS 渲染的页面。
    """
    logger.info(f"Attempting to fetch {url} using MCP browser_snapshot...")
    try:
        # 动态导入避免循环引用
        from mcp_client.manager import mcp_manager
        from mcp_client.playwright import register_playwright_server
        
        # 确保服务已注册
        register_playwright_server()
        
        # 步骤1：导航
        logger.info(f"MCP: Navigating to {url}...")
        await mcp_manager.call_tool("playwright", "browser_navigate", {"url": url})
        
        # 步骤2：等待加载
        logger.info("MCP: Waiting for page load...")
        try:
            await mcp_manager.call_tool("playwright", "browser_wait_for", {"time": 3})
        except Exception as e:
            logger.warning(f"MCP wait failed: {e}")
            
        # 步骤3：获取快照
        logger.info("MCP: Taking snapshot...")
        result = await mcp_manager.call_tool("playwright", "browser_snapshot", {})
        
        # 解析结果
        # browser_snapshot 通常返回 TextContent
        content = ""
        if isinstance(result, list):
            for item in result:
                if hasattr(item, 'text'):
                    content += item.text + "\n"
        elif hasattr(result, 'text'):
            content = result.text
            
        if content:
            logger.info(f"MCP snapshot successful, length: {len(content)}")
            return f"【通过 Playwright 获取的页面快照】\n\n{content}"
            
        return None
        
    except Exception as e:
        logger.error(f"MCP browser_snapshot failed: {e}")
        return None


async def fetch_webpage_content(url: str) -> str | None:
    """
    获取网页内容
    
    Args:
        url: 网页 URL
        
    Returns:
        网页文本内容，如果失败返回 None
    """
    # -----------------------------------------------------------------
    # 策略升级：域名特定路由 (Domain Specific Routers)
    # -----------------------------------------------------------------
    for domain, handler in DOMAIN_HANDLERS.items():
        if domain in url:
            logger.info(f"Using custom handler for domain: {domain}")
            return await handler(url)

    # -----------------------------------------------------------------
    # 策略升级：如果是 Google News 链接，先尝试解码还原真实 URL
    # -----------------------------------------------------------------
    if "news.google.com" in url or "google.com/news" in url:
        try:
            logger.info(f"Detected Google News URL, decoding with googlenewsdecoder: {url}")
            from googlenewsdecoder import gnewsdecoder
            # gnewsdecoder 是同步函数，包裹在 executor 中运行以免阻塞
            def decode_func():
                return gnewsdecoder(url, interval=1)
            
            decoded_result = await asyncio.to_thread(decode_func)
            
            if decoded_result.get("status"):
                real_url = decoded_result.get("decoded_url")
                if real_url:
                    logger.info(f"Successfully decoded Google News URL: {url} -> {real_url}")
                    url = real_url
            else:
                logger.warning(f"Google News decoding failed: {decoded_result.get('message')}")
        except Exception as e:
            logger.error(f"Error decoding Google News URL: {e}")

    # -----------------------------------------------------------------
    # 策略升级：如果是视频平台，优先尝试使用 yt-dlp 获取元数据
    # 这能解决 X (Twitter) 等前端渲染页面的抓取问题
    # -----------------------------------------------------------------
    if is_video_platform(url):
        logger.info(f"Detected video platform URL, trying yt-dlp extraction: {url}")
        video_content = await fetch_video_metadata(url)
        if video_content:
             return f"【从视频平台提取的元数据】\n{video_content}"
        logger.info("yt-dlp extraction failed, falling back to standard scraping.")

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            response.raise_for_status()
            
            # 解析 HTML
            soup = BeautifulSoup(response.text, "html.parser")
            
            # 移除脚本和样式
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()
            
            # 获取标题
            title = soup.title.string if soup.title else ""
            
            # 获取正文内容
            # 优先尝试 article 标签
            article = soup.find("article")
            if article:
                text = article.get_text(separator="\n", strip=True)
            else:
                # 否则获取 body 内容
                body = soup.find("body")
                if body:
                    text = body.get_text(separator="\n", strip=True)
                else:
                    text = soup.get_text(separator="\n", strip=True)
            
            # 限制文本长度（避免 token 超限）
            max_length = 8000
            if len(text) > max_length:
                text = text[:max_length] + "..."

            # -----------------------------------------------------------------
            # 增加网页有效性校验 (防止 AI 总结错误页面)
            # -----------------------------------------------------------------
            
            # 2. 检查常见错误关键字 (JavaScript, Error page, etc)
            error_keywords = [
                "JavaScript is disabled",
                "enable JavaScript",
                "browser is not supported",
                "Something went wrong",
                "Please wait...",
                "Just a moment...",
                "Checking your browser",
                "403 Forbidden",
                "404 Not Found",
                "Access Denied",
                "JavaScript 已经被禁用",
                "请启用 JavaScript",
                "Google News", # Google News interstitial page title often contains this
            ]
            
            # 检查前 500 个字符即可 (通常错误提示在最前面)
            preview_text = text[:500].lower()
            needs_fallback = False
            
            for ignored in error_keywords:
                if ignored.lower() in preview_text:
                    logger.warning(f"Detected invalid content ('{ignored}') for {url}")
                    needs_fallback = True
                    break
            
            # 1. 检查文本长度，太短也视为无效
            if len(text.strip()) < 50:
                logger.warning(f"Extracted content too short ({len(text)} chars) for {url}.")
                needs_fallback = True

            if needs_fallback:
                # 策略升级：使用 MCP Browser Snapshot 进行回退
                logger.info(f"Falling back to MCP browser_snapshot for {url}")
                snapshot_content = await fetch_with_browser_snapshot(url)
                if snapshot_content:
                    return snapshot_content
                
                # 如果 MCP 也失败，尝试 yt-dlp 兜底
                logger.info(f"MCP fallback failed, trying yt-dlp for {url}")
                video_content = await fetch_video_metadata(url)
                if video_content:
                    return f"【通过工具提取的元数据】\n{video_content}"
                return None
            
            return f"标题：{title}\n\n内容：\n{text}"
            
    except Exception as e:
        logger.error(f"Failed to fetch webpage: {e}")
        
        # 出错时优先尝试 MCP Browser Snapshot
        try:
             logger.info(f"Exception occurred, trying MCP browser_snapshot for {url}")
             snapshot_content = await fetch_with_browser_snapshot(url)
             if snapshot_content:
                 return snapshot_content
        except Exception as mcp_e:
             logger.error(f"MCP fallback also failed: {mcp_e}")

        # 最后尝试 yt-dlp
        try:
             video_content = await fetch_video_metadata(url)
             if video_content:
                 return f"【通过工具提取的元数据】\n{video_content}"
        except:
             pass
        return None


async def summarize_webpage(url: str) -> str:
    """
    获取网页并生成摘要
    
    Args:
        url: 网页 URL
        
    Returns:
        摘要文本
    """
    # 获取网页内容
    content = await fetch_webpage_content(url)
    if not content:
        return f"❌ 无法获取网页内容：{url}"
    
    try:
        # 定制化 Prompt：针对 Fina 财经快讯，使用列表格式而非摘要
        if "fina.ifnet.top" in url:
             prompt = f"以下是获取到的实时财经快讯数据（JSON格式）。请将其整理为一份清晰的**新闻列表**发送给用户。\n\n数据内容：\n{content}"
             system_instruction = (
                 "你是一个财经资讯助手。\n"
                 "用户提供了一组 JSON 格式的新闻数据。\n"
                 "请直接将其整理为编号列表，每条新闻包含：\n"
                 "1. 标题/内容核心 (去除 HTML 标签)\n"
                 "2. 时间 (如果有)\n"
                 "3. 来源 (如果有)\n"
                 "不需要进行通过性的总结，只需要清晰展示列表。保留前 10-15 条最重要的即可。"
             )
        else:
             prompt = f"请为以下网页内容生成简洁的中文摘要：\n\n{content}"
             system_instruction = (
                    "你是一个专业的内容摘要助手。"
                    "请生成简洁、准确的中文摘要，包含以下要点：\n"
                    "1. 主题是什么\n"
                    "2. 主要观点或内容\n"
                    "3. 关键信息\n"
                    "摘要应该简洁明了，一般不超过 200 字。"
                )

        # 使用 Gemini 生成摘要
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "system_instruction": system_instruction,
            },
        )
        
        if response.text:
            return f"📄 **网页摘要**\n\n🔗 {url}\n\n{response.text}"
        else:
            return f"❌ 无法生成摘要：{url}"
            
    except Exception as e:
        logger.error(f"Failed to summarize webpage: {e}")
        return f"❌ 摘要生成失败：{url}"
