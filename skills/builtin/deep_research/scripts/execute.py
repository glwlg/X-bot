import asyncio
import logging
from urllib.parse import quote
import httpx
from core.platform.models import UnifiedContext
from utils import smart_reply_text
from services.web_summary_service import fetch_webpage_content
from core.config import gemini_client, GEMINI_MODEL

logger = logging.getLogger(__name__)

async def execute(ctx: UnifiedContext, params: dict) -> str:
    topic = params.get("topic", "").strip()
    depth = params.get("depth", 3)
    language = params.get("language", "zh-CN")
    
    if not topic:
        await ctx.reply("❌ 请提供研究主题 (topic)")
        return "Failed: No topic provided."
        
    depth = min(max(1, int(depth)), 5) # 限制 1-5
    
    await ctx.reply(f"🧐 正在对 「{topic}」 进行深度研究 (深度: {depth})...\n此过程包含：搜索 -> 爬取网页 -> 深度阅读 -> 综合报告，可能需要 30-60 秒，请耐心等待。")
    
    # 1. Search Phase
    search_results = []
    try:
        encoded_query = quote(topic)
        # Always use general + news categories for research
        search_url = f"http://192.168.1.100:28080/search?q={encoded_query}&format=json&categories=general,news,it,science&time_range=year&language={language}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(search_url)
            if response.status_code == 200:
                data = response.json()
                search_results = data.get("results", [])[:depth]
            else:
                await ctx.reply(f"⚠️ 搜索阶段失败 (Status: {response.status_code})，尝试继续...")
    except Exception as e:
        logger.error(f"Search failed: {e}")
        await ctx.reply(f"⚠️ 搜索阶段出错: {e}")
        
    if not search_results:
        await ctx.reply("❌ 未找到相关搜索结果，研究终止。")
        return f"Failed: No search results found for topic '{topic}'."

    # 2. Crawl Phase
    await ctx.reply(f"🕷️ 正在爬取并阅读 {len(search_results)} 个网页...")
    
    crawled_data = []
    
    async def process_url(item):
        url = item.get("url")
        title = item.get("title", "No Title")
        try:
            content = await fetch_webpage_content(url)
            if content:
                return {
                    "title": title,
                    "url": url,
                    "content": content
                }
        except Exception as e:
            logger.error(f"Crawl failed for {url}: {e}")
        return None

    crawled_results = await asyncio.gather(*(process_url(item) for item in search_results))
    valid_data = [item for item in crawled_results if item]
    
    if not valid_data:
        await ctx.reply("❌ 无法读取任何网页内容（可能是因为反爬虫或网络问题），研究终止。")
        return f"Failed: Unable to crawl any content for topic '{topic}'."

    # 3. Synthesis Phase
    await ctx.reply(f"🧠 已获取 {len(valid_data)} 份资料，正在综合分析并撰写报告...")
    
    # Construct Context
    context_text = f"Research Topic: {topic}\n\nSources Data:\n"
    for i, data in enumerate(valid_data, 1):
        context_text += f"\n--- Source {i}: {data['title']} ---\nURL: {data['url']}\nContent:\n{data['content'][:15000]}\n" # Limit per page to avoid insanity
        
    prompt = f"""
    You are a Deep Research Analyst. Your task is to write a comprehensive Deep Dive Report on the topic: "{topic}".
    
    Based ONLY on the provided source materials below, write a detailed, structured, and professional report.
    
    Report Structure:
    1. **Executive Summary**: High-level overview of key findings.
    2. **Detailed Analysis**: Break down the topic into key aspects (e.g., Architecture, Performance, Pros/Cons, History).
    3. **Key Insights**: What are the most important takeaways?
    4. **Source Discrepancies** (if any): Did sources disagree?
    5. **Reference List**: List the titles and URLs of sources used.
    
    Format output as HTML (for a standalone report file). Use modern, clean CSS.
    Title the HTML page "Deep Research: {topic}".
    Ensure the HTML is self-contained.
    
    Source Material:
    {context_text}
    """
    
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        
        report_html = response.text
        
        # Strip markdown code blocks if AI added them
        import re
        report_html = re.sub(r"^```html\s*", "", report_html)
        report_html = re.sub(r"^```\s*", "", report_html)
        report_html = re.sub(r"\s*```$", "", report_html)
        
        # Output
        import io
        file_obj = io.BytesIO(report_html.encode('utf-8'))
        file_obj.name = "deep_research_report.html"
        
        await ctx.reply_document(
            document=file_obj,
            caption=f"📚 深度研究报告：{topic}\n\n基于 {len(valid_data)} 个来源的深度综合分析。"
        )
        
        return f"Success: Deep research report generated for '{topic}' based on {len(valid_data)} sources."
        
    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        await ctx.reply(f"❌ 报告生成阶段失败: {e}")
        return f"Failed: Synthesis error: {e}"
