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
