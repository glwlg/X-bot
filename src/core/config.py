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
ROUTING_MODEL = os.getenv("ROUTING_MODEL", "gemini-2.0-flash")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "imagen-3.0-generate-002")
# 代码生成模型（用于 Skill 创建，建议使用更强力的模型）
CREATOR_MODEL = os.getenv("CREATOR_MODEL", "gemini-2.5-pro-preview-05-06")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set!")

# 初始化 Gemini 客户端
gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"api_version": "v1beta", "base_url": GEMINI_BASE_URL},
)

# 用户访问控制
# 从环境变量加载管理员列表
ADMIN_USER_IDS_STR = os.getenv("ADMIN_USER_IDS") or os.getenv("ALLOWED_USER_IDS", "")
ADMIN_USER_IDS = set()
if ADMIN_USER_IDS_STR.strip():
    ADMIN_USER_IDS = {
        int(uid.strip()) for uid in ADMIN_USER_IDS_STR.split(",") if uid.strip()
    }


async def is_user_allowed(user_id: int) -> bool:
    """
    检查用户是否有权限使用 Bot
    权限逻辑：管理员 OR 在数据库白名单中
    """
    # 1. 如果是管理员，直接允许
    if user_id in ADMIN_USER_IDS:
        return True
        
    # 2. 如果没有设置任何管理员，且没有启用白名单模式（默认），是否允许所有人？
    # 现在的逻辑是：如果设置了 ADMIN_USER_IDS，则作为白名单基础。
    # 之前的逻辑：如果 ALLOWED_USER_IDS 为空，允许所有人。
    # 我们保持兼容：如果 ADMIN_USER_IDS 为空，且数据库白名单也为空，则允许所有人？
    # 或者如果不设置 Admin，则没人能管理，但大家都能用？
    # User Request: "Allowed user variable changed to Admin list"
    # Let's assume strict mode if Admin is set.
    
    # 检查数据库
    from repositories import check_user_allowed_in_db
    try:
        if await check_user_allowed_in_db(user_id):
            return True
    except Exception:
        # DB 可能还没初始化或出错
        pass
        
    # 如果管理员列表为空，是否开放？
    # 为了安全，如果有 DB 但不在里面，且有 Admin 设置，则拒绝。
    # 如果 Admin 也没设置，通常意味着开放模式。
    if not ADMIN_USER_IDS:
        return True
        
    return False


def is_user_admin(user_id: int) -> bool:
    """检查用户是否为管理员"""
    return user_id in ADMIN_USER_IDS

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
WAITING_FOR_FEATURE_INPUT = 6

# MCP 配置
MCP_ENABLED = os.getenv("MCP_ENABLED", "true").lower() == "true"
MCP_PLAYWRIGHT_IMAGE = os.getenv("MCP_PLAYWRIGHT_IMAGE", "mcr.microsoft.com/playwright/mcp:1.51.0-noble")
MCP_MEMORY_ENABLED = os.getenv("MCP_MEMORY_ENABLED", "true").lower() == "true"
MCP_TIMEOUT_SECONDS = int(os.getenv("MCP_TIMEOUT_SECONDS", "60"))

# 确保目录存在
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
