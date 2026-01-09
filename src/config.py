"""
配置模块 - 管理环境变量和常量
"""
import os
from dotenv import load_dotenv
from google import genai

# 加载环境变量（如果 .env 文件存在）
# Docker 容器中通过 docker-compose 的 env_file 直接注入环境变量
load_dotenv(override=False)

# Telegram Bot 配置
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")

# Gemini API 配置
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "imagen-3.0-generate-002")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set!")

# 初始化 Gemini 客户端
gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"api_version": "v1beta", "base_url": GEMINI_BASE_URL},
)

# 用户访问控制
ALLOWED_USER_IDS_STR = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = set()
if ALLOWED_USER_IDS_STR.strip():
    # 解析逗号分隔的用户 ID 列表
    ALLOWED_USER_IDS = {
        int(uid.strip()) for uid in ALLOWED_USER_IDS_STR.split(",") if uid.strip()
    }


def is_user_allowed(user_id: int) -> bool:
    """检查用户是否在白名单中"""
    # 如果白名单为空，允许所有用户
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

# 下载配置
DOWNLOAD_DIR = "downloads"
DATA_DIR = "data"
PERMANENT_STORAGE_DIR = "/app/media"  # For files > 49MB
UPDATE_INTERVAL_SECONDS = 2  # 进度更新间隔（秒）
MAX_FILE_SIZE_MB = 49  # Telegram 最大文件大小限制
COOKIES_FILE = "cookies.txt"  # yt-dlp cookies file path

# 会话状态常量
WAITING_FOR_VIDEO_URL = 1
WAITING_FOR_IMAGE_PROMPT = 2
WAITING_FOR_REMIND_INPUT = 3
WAITING_FOR_MONITOR_KEYWORD = 4
WAITING_FOR_SUBSCRIBE_URL = 5

# 确保目录存在
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

