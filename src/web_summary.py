"""
网页摘要模块 - 提取网页内容并使用 AI 生成摘要
"""
import re
import logging
import httpx
from bs4 import BeautifulSoup

from config import gemini_client, GEMINI_MODEL

logger = logging.getLogger(__name__)

# URL 正则表达式
URL_PATTERN = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+'
)


def extract_urls(text: str) -> list[str]:
    """从文本中提取 URL"""
    return URL_PATTERN.findall(text)


async def fetch_webpage_content(url: str) -> str | None:
    """
    获取网页内容
    
    Args:
        url: 网页 URL
        
    Returns:
        网页文本内容，如果失败返回 None
    """
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
            
            return f"标题：{title}\n\n内容：\n{text}"
            
    except Exception as e:
        logger.error(f"Failed to fetch webpage: {e}")
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
        # 使用 Gemini 生成摘要
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"请为以下网页内容生成简洁的中文摘要：\n\n{content}",
            config={
                "system_instruction": (
                    "你是一个专业的内容摘要助手。"
                    "请生成简洁、准确的中文摘要，包含以下要点：\n"
                    "1. 主题是什么\n"
                    "2. 主要观点或内容\n"
                    "3. 关键信息\n"
                    "摘要应该简洁明了，一般不超过 200 字。"
                ),
            },
        )
        
        if response.text:
            return f"📄 **网页摘要**\n\n🔗 {url}\n\n{response.text}"
        else:
            return f"❌ 无法生成摘要：{url}"
            
    except Exception as e:
        logger.error(f"Failed to summarize webpage: {e}")
        return f"❌ 摘要生成失败：{url}"
