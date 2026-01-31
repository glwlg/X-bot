import re


def markdown_to_telegram_html(text: str) -> str:
    """
    将标准 Markdown 转换为 Telegram 支持的 HTML 格式
    使用 Masking 策略防止 regex 误伤代码块
    """
    if not text:
        return ""

    # 1. HTML 转义
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 2. 保护代码块
    # 使用 UUID 风格的特殊标记，避免与 markdown 语法冲突
    MASK_PREFIX = "🔒M_A_S_K_"
    placeholders = []

    def mask_code_block(match):
        idx = len(placeholders)
        # Group 2 is content
        content = match.group(2)
        placeholders.append(f"<pre>{content}</pre>")
        return f"{MASK_PREFIX}{idx}🔒"

    def mask_inline_code(match):
        idx = len(placeholders)
        content = match.group(1)
        placeholders.append(f"<code>{content}</code>")
        return f"{MASK_PREFIX}{idx}🔒"

    # Code block (```)
    text = re.sub(r"```(\w+)?\n?(.*?)```", mask_code_block, text, flags=re.DOTALL)
    # Inline Code (`)
    text = re.sub(r"`([^`]+)`", mask_inline_code, text)

    # 3. 替换 Markdown 语法

    # Bold (**)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    # Bold (__) - strict match to avoid partials
    text = re.sub(r"(?<!_)__([^_]+)__(?!_)", r"<b>\1</b>", text)

    # Italic (*)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)

    # Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Headers
    text = re.sub(r"^(#+)\s+(.*?)$", r"<b>\2</b>", text, flags=re.MULTILINE)

    # 4. 还原代码块
    for i, replacement in enumerate(placeholders):
        key = f"{MASK_PREFIX}{i}🔒"
        text = text.replace(key, replacement)

    return text
