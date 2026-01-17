"""
工具函数模块
"""
import re

# URL 正则表达式
URL_REGEX = re.compile(
    r"(https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/]+/status/\d+|"
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/[\w-]+|"
    r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+|"
    r"https?://(?:www\.|m\.)?(?:tiktok\.com|douyin\.com)/.+/video/\d+|"
    r"https?://vt\.tiktok\.com/[\w\d]+/?|"
    r"https?://(?:www\.)?bilibili\.com/video/BV[\w]+)"
)


def create_progress_bar(percentage: float) -> str:
    """创建文本进度条"""
    filled_length = int(10 * percentage // 100)
    bar = "█" * filled_length + "░" * (10 - filled_length)
    return f"下载中: [{bar}] {percentage:.1f}%"


def is_video_url(text: str) -> bool:
    """检查文本是否包含视频 URL"""
    return URL_REGEX.search(text) is not None


def extract_video_url(text: str) -> str | None:
    """从文本中提取视频 URL"""
    match = URL_REGEX.search(text)
    return match.group(0) if match else None


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
    
    # Bullet points (standardize)
    # text = re.sub(r'^\s*[-*]\s+', '• ', text, flags=re.MULTILINE)
    
    return text


async def smart_edit_text(message, text: str, reply_markup=None):
    """
    智能编辑消息函数
    1. 尝试使用 Telegram HTML 模式 (通过转换)
    2. 失败则降级为纯文本
    """
    html_text = markdown_to_telegram_html(text)
    try:
        if len(text) > 4000:
            # 简单的截断，避免超出 Telegram 限制
            html_text = html_text[:4000] + "...(content truncated)"
            
        return await message.edit_text(
            html_text, 
            parse_mode="HTML", 
            reply_markup=reply_markup,
            disable_web_page_preview=True 
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
             # Try plain text fallback
             try:
                 # Also truncate plain text to avoid length errors
                 safe_text = text[:4000] + "...(truncated)" if len(text) > 4000 else text
                 return await message.edit_text(
                    safe_text, 
                    parse_mode=None, 
                    reply_markup=reply_markup
                 )
             except Exception as inner_e:
                 # Log the error if fallback fails
                 import logging
                 logging.getLogger(__name__).error(f"smart_edit_text fallback failed: {inner_e}")
                 pass
        return None

async def smart_reply_text(update, text: str, reply_markup=None):
    """
    智能回复消息函数 (类似 smart_edit_text)
    支持普通消息和 callback_query
    """
    html_text = markdown_to_telegram_html(text)
    
    # 获取回复目标：优先使用 message，否则使用 callback_query.message
    target_message = None
    if update.message:
        target_message = update.message
    elif update.callback_query and update.callback_query.message:
        target_message = update.callback_query.message
    
    if not target_message:
        import logging
        logging.getLogger(__name__).error("smart_reply_text: no message to reply to")
        return None
    
    try:
        return await target_message.reply_text(
            html_text, 
            parse_mode="HTML", 
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    except Exception as e:
        # Fallback to plain text
        try:
             return await target_message.reply_text(
                text, 
                parse_mode=None, 
                reply_markup=reply_markup
            )
        except Exception as inner_e:
            import logging
            logging.getLogger(__name__).error(f"smart_reply_text fallback failed: {inner_e}")
        return None
