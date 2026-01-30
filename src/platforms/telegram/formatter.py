import re

def markdown_to_telegram_html(text: str) -> str:
    """
    将标准 Markdown 转换为 Telegram 支持的 HTML 格式
    此函数仅支持基础的 Markdown 语法，旨在提高 Telegram 渲染成功率
    
    支持:
    - **Bold** -> <b>Bold</b>
    - *Italic* -> <i>Italic</i>
    - `Code` -> <code>Code</code>
    - [Link](Url) -> <a href="Url">Link</a>
    - # Header -> <b>Header</b>
    """
    if not text:
        return ""
        
    # 1. HTML 转义 (关键，防止 < > & 破坏 HTML 结构)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # 2. 替换 Markdown 语法
    
    # Code block (```) -> <pre>
    text = re.sub(r'```(\w+)?\n?(.*?)```', r'<pre>\2</pre>', text, flags=re.DOTALL)
    
    # Inline Code (`) -> <code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    
    # Bold (**) -> <b>
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    
    # Bold (__ is sometimes used)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    
    # Italic (*) -> <i>
    # 只有成对的 * 且中间没有换行才视为 Italic
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', text)
    
    # Links [text](url) -> <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    
    # Headers (#) -> Bold
    text = re.sub(r'^(#+)\s+(.*?)$', r'<b>\2</b>', text, flags=re.MULTILINE)
    
    return text
