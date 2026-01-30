from telegram import Update
from telegram.ext import ContextTypes
from services.web_summary_service import fetch_webpage_content, summarize_webpage

async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> str:
    """执行网页浏览任务"""
    action = params.get("action", "visit")
    url = params.get("url")
    
    if not url:
        return "❌ 请提供 URL"
        
    # 简单的 URL 补全
    if not url.startswith("http"):
        url = "https://" + url

    if action == "summarize":
        # 调用 summarize_webpage，它内部已经处理了 fetch 和 AI 生成摘要
        # 并且会返回格式化好的文本
        result = await summarize_webpage(url)
        return result

    elif action == "visit":
        # 获取原始内容，供 Agent 进一步处理（例如回答特定问题）
        content = await fetch_webpage_content(url)
        if content:
            # 截断过长内容，避免在这里这就爆掉 Token，
            # 虽然 fetch_webpage_content 内部有限制 (8000 chars)，但作为 Tool Output 还是要注意
            # 这里直接返回，Agent 会看到 Tool Output
            return f"【网页内容 - {url}】\n\n{content}"  
        else:
            return f"❌ 无法访问该网页：{url}"
            
    else:
        return f"❌ 未知操作：{action}"
