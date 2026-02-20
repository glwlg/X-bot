"""
配置模块 - 管理环境变量和常量
"""

import os
from dotenv import load_dotenv

try:
    from openai import AsyncOpenAI, OpenAI  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover - optional during migration bootstrap
    AsyncOpenAI = None
    OpenAI = None

# 加载环境变量（如果 .env 文件存在）
# Docker 容器中通过 docker-compose 的 env_file 直接注入环境变量
load_dotenv(override=False)

# Telegram Bot 配置
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Discord Bot 配置
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# DingTalk (钉钉) Stream Mode 配置
DINGTALK_CLIENT_ID = os.getenv("DINGTALK_CLIENT_ID")
DINGTALK_CLIENT_SECRET = os.getenv("DINGTALK_CLIENT_SECRET")

# 日志配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or GEMINI_BASE_URL
LLM_API_KEY = os.getenv("LLM_API_KEY") or GEMINI_API_KEY
CORE_MODEL = os.getenv("CORE_MODEL", "gpt-4o-mini")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", CORE_MODEL)

ROUTING_MODEL = os.getenv("ROUTING_MODEL", CORE_MODEL)
# 代码生成模型（用于 Skill 创建，建议使用更强力的模型）
CREATOR_MODEL = os.getenv("CREATOR_MODEL", CORE_MODEL)

IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL") or os.getenv("IMAGE_MODEL", "gpt-image-1")


if not LLM_API_KEY:
    raise ValueError(
        "LLM_API_KEY (or legacy GEMINI_API_KEY) environment variable not set!"
    )

if OpenAI is not None and AsyncOpenAI is not None:
    openai_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or None)
    openai_async_client = AsyncOpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL or None,
    )
else:
    openai_client = None
    openai_async_client = None

# 用户访问控制
# 从环境变量加载管理员列表
ADMIN_USER_IDS_STR = os.getenv("ADMIN_USER_IDS") or os.getenv("ALLOWED_USER_IDS", "")
ADMIN_USER_IDS = set()
if ADMIN_USER_IDS_STR.strip():
    ADMIN_USER_IDS = {
        uid.strip() for uid in ADMIN_USER_IDS_STR.split(",") if uid.strip()
    }


async def is_user_allowed(user_id: int | str) -> bool:
    """
    检查用户是否有权限使用 Bot
    权限逻辑：管理员 OR 在白名单存储中
    """
    uid_str = str(user_id).strip()

    # 1. 如果是管理员，直接允许
    if uid_str in ADMIN_USER_IDS:
        return True

    # 2. 如果没有设置任何管理员，且没有启用白名单模式（默认），是否允许所有人？
    # 现在的逻辑是：如果设置了 ADMIN_USER_IDS，则作为白名单基础。

    # 检查白名单存储
    from core.state_store import check_user_allowed_in_db

    try:
        # 存储层支持 str user_id
        if await check_user_allowed_in_db(uid_str):
            return True
    except Exception:
        # 存储层可能还没初始化或出错
        pass

    # 如果管理员列表为空，是否开放？
    # 为了安全，如果有 DB 但不在里面，且有 Admin 设置，则拒绝。
    # 如果 Admin 也没设置，通常意味着开放模式。
    if not ADMIN_USER_IDS:
        return True

    return False


def is_user_admin(user_id: int | str) -> bool:
    """检查用户是否为管理员"""
    return str(user_id).strip() in ADMIN_USER_IDS


# 下载配置
def _is_docker_runtime() -> bool:
    return os.path.exists("/.dockerenv") or (
        os.getenv("RUNNING_IN_DOCKER", "").lower() == "true"
    )


def _default_data_dir() -> str:
    app_root = "/app"
    app_data = "/app/data"
    if (
        _is_docker_runtime()
        and os.path.isdir(app_root)
        and os.access(app_root, os.W_OK | os.X_OK)
    ):
        return app_data
    if os.path.isdir(app_data) and os.access(app_data, os.W_OK | os.X_OK):
        return app_data
    return "data"


DOWNLOAD_DIR = "downloads"
DATA_DIR = os.getenv("DATA_DIR", _default_data_dir())
PERMANENT_STORAGE_DIR = "/app/media"  # For files > 49MB
UPDATE_INTERVAL_SECONDS = 2  # 进度更新间隔（秒）
MAX_FILE_SIZE_MB = 49  # Telegram 最大文件大小限制
COOKIES_FILE = os.path.join(DATA_DIR, "cookies.txt")  # yt-dlp cookies file path

# 会话状态常量
WAITING_FOR_VIDEO_URL = 1
WAITING_FOR_REMIND_INPUT = 3
WAITING_FOR_MONITOR_KEYWORD = 4
WAITING_FOR_SUBSCRIBE_URL = 5
WAITING_FOR_FEATURE_INPUT = 6

# Internal Search Service URL (SearXNG)
SEARXNG_URL = os.getenv("SEARXNG_URL")
# Skill Context Injection Mode: 'full' (default), 'search_first', 'compact'
SKILL_INJECTION_MODE = os.getenv("SKILL_INJECTION_MODE", "full")

# Server IP Override (Optional, for fixed deployment)
SERVER_IP = os.getenv("SERVER_IP")

# Deployment Staging Path (Optional, for Docker deployment feature)
# This path is used for cloning and building repositories
# Must be an absolute path that matches the Docker volume mount
X_DEPLOYMENT_STAGING_PATH = os.getenv("X_DEPLOYMENT_STAGING_PATH")

# Heartbeat runtime configuration
HEARTBEAT_ENABLED = os.getenv("HEARTBEAT_ENABLED", "true").lower() == "true"
HEARTBEAT_EVERY = os.getenv("HEARTBEAT_EVERY", "30m")
HEARTBEAT_TARGET = os.getenv("HEARTBEAT_TARGET", "last")
HEARTBEAT_ACTIVE_START = os.getenv("HEARTBEAT_ACTIVE_START", "08:00")
HEARTBEAT_ACTIVE_END = os.getenv("HEARTBEAT_ACTIVE_END", "22:00")
HEARTBEAT_TIMEZONE = os.getenv("HEARTBEAT_TIMEZONE", "")
HEARTBEAT_TICK_SEC = int(os.getenv("HEARTBEAT_TICK_SEC", "30"))
HEARTBEAT_SUPPRESS_OK = os.getenv("HEARTBEAT_SUPPRESS_OK", "true").lower() == "true"
HEARTBEAT_MODE = os.getenv("HEARTBEAT_MODE", "readonly").strip().lower() or "readonly"
HEARTBEAT_READONLY_DISPATCH = (
    os.getenv("HEARTBEAT_READONLY_DISPATCH", "false").lower() == "true"
)

# Auto recovery budget for terminal/recoverable failures in orchestrator loop
AUTO_RECOVERY_MAX_ATTEMPTS = int(os.getenv("AUTO_RECOVERY_MAX_ATTEMPTS", "3"))

# Dual-layer worker runtime configuration
USERLAND_ROOT = os.getenv(
    "USERLAND_ROOT", os.path.join(DATA_DIR, "userland", "workers")
)
WORKER_DEFAULT_BACKEND = os.getenv("WORKER_DEFAULT_BACKEND", "core-agent")
WORKER_EXEC_TIMEOUT_SEC = int(os.getenv("WORKER_EXEC_TIMEOUT_SEC", "900"))
WORKER_RUNTIME_MODE = (
    os.getenv("WORKER_RUNTIME_MODE", "local").strip().lower() or "local"
)
WORKER_DOCKER_CONTAINER = os.getenv("WORKER_DOCKER_CONTAINER", "x-bot-worker")
WORKER_DOCKER_DATA_DIR = os.getenv("WORKER_DOCKER_DATA_DIR", "/app/data")
WORKER_CODEX_COMMAND = os.getenv("WORKER_CODEX_COMMAND", "codex")
WORKER_GEMINI_CLI_COMMAND = os.getenv("WORKER_GEMINI_CLI_COMMAND", "gemini-cli")
WORKER_SHELL_COMMAND = os.getenv("WORKER_SHELL_COMMAND", "sh")
WORKER_CODEX_ARGS_TEMPLATE = os.getenv(
    "WORKER_CODEX_ARGS_TEMPLATE", "exec {instruction}"
)
WORKER_GEMINI_ARGS_TEMPLATE = os.getenv(
    "WORKER_GEMINI_ARGS_TEMPLATE", "--prompt {instruction}"
)
WORKER_AUTH_STATUS_TIMEOUT_SEC = int(os.getenv("WORKER_AUTH_STATUS_TIMEOUT_SEC", "45"))
WORKER_CODEX_AUTH_START_ARGS = os.getenv("WORKER_CODEX_AUTH_START_ARGS", "auth login")
WORKER_GEMINI_AUTH_START_ARGS = os.getenv("WORKER_GEMINI_AUTH_START_ARGS", "auth login")
WORKER_CODEX_AUTH_STATUS_ARGS = os.getenv(
    "WORKER_CODEX_AUTH_STATUS_ARGS", "auth status"
)
WORKER_GEMINI_AUTH_STATUS_ARGS = os.getenv(
    "WORKER_GEMINI_AUTH_STATUS_ARGS", "auth status"
)
WORKER_FALLBACK_CORE_AGENT = (
    os.getenv("WORKER_FALLBACK_CORE_AGENT", "true").lower() == "true"
)

# Core chat dispatch policy:
# - worker_only: always dispatch to worker, no fallback
# - worker_preferred: dispatch worker first, fallback to core orchestrator on worker failure
# - orchestrator: keep current core orchestrator execution path
CORE_CHAT_EXECUTION_MODE = (
    os.getenv("CORE_CHAT_EXECUTION_MODE", "worker_only").strip().lower()
    or "worker_only"
)
CORE_CHAT_WORKER_BACKEND = (
    os.getenv("CORE_CHAT_WORKER_BACKEND", "core-agent").strip().lower() or "core-agent"
)

# Kernel-protected source roots (comma-separated absolute/relative paths)
KERNEL_PROTECTED_PATHS = os.getenv("KERNEL_PROTECTED_PATHS", "")

# 确保目录存在
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
