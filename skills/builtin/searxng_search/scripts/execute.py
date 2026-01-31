import asyncio
from urllib.parse import quote
import httpx
from core.platform.models import UnifiedContext


async def execute(ctx: UnifiedContext, params: dict) -> None:
    query = params.get("query", "").strip()
    queries = params.get("queries", [])
    num_results = params.get("num_results", 5)
    categories = params.get("categories", "general")
    time_range = params.get("time_range", "")
    language = params.get("language", "zh-CN")

    # Normalize input
    if isinstance(queries, str):
        queries = [queries]

    # If single query provided but no queries list
    if query and not queries:
        queries = [query]

    queries = [q for q in queries if q.strip()]

    # Check Configuration
    import os

    # Try getting from core.config if possible, or env
    # Since this runs in same process, we can import core.config?
    # Or just use os.getenv which matches core.config logic mostly.
    # User asked to disable if not configured.
    base_url = os.getenv("SEARXNG_URL")
    if not base_url:
        await ctx.reply("⚠️ 搜索服务未配置 (SEARXNG_URL missing). 技能暂时不可用。")
        return "Search service is disabled by configuration."

    if not queries:
        await ctx.reply("❌ 请提供搜索关键词")
        return

    # Limit queries count
    queries = queries[:5]
    num_results = min(max(1, int(num_results)), 10)

    status_msg = (
        f"🔍 正在搜索 {len(queries)} 个主题..."
        if len(queries) > 1
        else f"🔍 正在搜索: {queries[0]}"
    )
    await ctx.reply(status_msg)

    async def fetch_results(search_query):
        try:
            encoded_query = quote(search_query)
            # Use configured URL
            # Ensure base_url doesn't have trailing slash if we append /search?
            # Adjust depending on if SEARXNG_URL includes /search or not.
            # Config default was "http://.../search". Let's assume env var provides full search endpoint or base.
            # Ideally user provides "http://host:port". We might need to append "/search".
            # Implementation assumes SEARXNG_URL is the full endpoint like "http://x:y/search" or we construct it.
            # Let's standardize: check if it ends with /search.

            nonlocal base_url
            if not base_url.endswith("/search"):
                if not base_url.endswith("/"):
                    base_url += "/"
                base_url += "search"

            search_url = f"{base_url}?q={encoded_query}&format=json"

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

                # Fallback mechanism: If specific category yields no results, try 'general'
                if not results and categories and categories != "general":
                    retry_url = (
                        f"{base_url}?q={encoded_query}&format=json&categories=general"
                    )
                    if time_range:
                        retry_url += f"&time_range={time_range}"
                    if language:
                        retry_url += f"&language={language}"

                    response = await client.get(retry_url)
                    if time_range:
                        retry_url += f"&time_range={time_range}"
                    if language:
                        retry_url += f"&language={language}"

                    response = await client.get(retry_url)
                    if response.status_code == 200:
                        data = response.json()
                        results = data.get("results", [])[:num_results]

                return search_query, results
        except Exception as e:
            return search_query, []

    # Concurrent Execution
    results_list = await asyncio.gather(*(fetch_results(q) for q in queries))

    # Build HTML Report
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Search Report</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { font-size: 24px; border-bottom: 2px solid #eee; padding-bottom: 10px; }
            h2 { font-size: 20px; color: #0066cc; margin-top: 30px; }
            .result-item { margin-bottom: 20px; padding: 15px; background: #f9f9f9; border-radius: 8px; border-left: 4px solid #0066cc; }
            .title { font-weight: bold; font-size: 16px; display: block; margin-bottom: 5px; text-decoration: none; color: #333; }
            .title:hover { text-decoration: underline; color: #0066cc; }
            .meta { font-size: 12px; color: #666; margin-bottom: 5px; }
            .content { font-size: 14px; color: #444; }
            .source { display: inline-block; background: #e0e0e0; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-right: 5px; }
            .empty { color: #888; font-style: italic; }
        </style>
    </head>
    <body>
        <h1>🔍 搜索聚合报告</h1>
    """

    agent_summary_lines = []

    found_any = False
    for query_text, res_items in results_list:
        html_content += f"<h2>Results for: {query_text}</h2>"
        agent_summary_lines.append(f"## 搜索: {query_text}")

        if not res_items:
            html_content += '<div class="empty">未找到相关结果</div>'
            agent_summary_lines.append("> 无结果")
            continue

        found_any = True
        for item in res_items:
            title = item.get("title", "No Title")
            url = item.get("url", "#")
            content = item.get("content", "")
            engine = item.get("engine", "unknown")
            pub_date = item.get("publishedDate", "")

            html_content += f"""
            <div class="result-item">
                <a href="{url}" class="title" target="_blank">{title}</a>
                <div class="meta">
                    <span class="source">{engine}</span>
                    {f"<span>🕒 {pub_date}</span>" if pub_date else ""}
                </div>
                <div class="content">{content[:300] + "..." if len(content) > 300 else content}</div>
            </div>
            """

            # Agent Summary (simplified)
            agent_summary_lines.append(f"- [{title}]({url})\n  {content[:100]}...")

    html_content += "</body></html>"

    if not found_any:
        await ctx.reply("😔 所有查询均未找到结果")
        return "No results found for any query."

    # Send HTML File
    try:
        import io

        file_obj = io.BytesIO(html_content.encode("utf-8"))
        file_obj.name = "search_report.html"

        # Platform specific internal logic
        is_discord = False
        try:
            # Try to detect via context (UnifiedContext abstraction can be leaky)
            if ctx.message.platform == "discord":
                is_discord = True
        except:
            pass

        await ctx.reply_document(
            document=file_obj, caption=f"📊 聚合搜索完成 ({len(queries)} 个主题)"
        )

        # For Discord, also send the content as text chunks because HTML isn't viewable
        if is_discord:
            markdown_report = ""
            for query_text, res_items in results_list:
                markdown_report += f"**🔎 Results for: {query_text}**\n"
                if not res_items:
                    markdown_report += "> *No results found*\n\n"
                    continue

                for item in res_items:
                    title = item.get("title", "No Title")
                    url = item.get("url", "#")
                    content = item.get("content", "").replace("\n", " ")
                    markdown_report += f"- **[{title}]({url})**\n"
                    markdown_report += f"  {content[:200]}...\n\n"

            # Split and send (approx 1900 chars limit for safety)
            chunks = []
            current_chunk = ""
            for line in markdown_report.split("\n"):
                if len(current_chunk) + len(line) + 1 > 1900:
                    chunks.append(current_chunk)
                    current_chunk = line + "\n"
                else:
                    current_chunk += line + "\n"
            if current_chunk:
                chunks.append(current_chunk)

            for chunk in chunks:
                if chunk.strip():
                    await ctx.reply(chunk.strip())

    except Exception as e:
        await ctx.reply(f"⚠️ 发送报告失败: {e}")

    return "\n".join(agent_summary_lines)
