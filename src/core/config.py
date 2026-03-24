"""
配置模块 - 管理环境变量和常量

模型配置已统一迁移到 config/models.json
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except Exception:
        return default


# Weixin (微信 iLink Bot) 配置
WEIXIN_ENABLE = os.getenv("WEIXIN_ENABLE", "false").lower() == "true"
WEIXIN_BASE_URL = os.getenv("WEIXIN_BASE_URL", "https://ilinkai.weixin.qq.com/")
WEIXIN_CDN_BASE_URL = os.getenv(
    "WEIXIN_CDN_BASE_URL", "https://novac2c.cdn.weixin.qq.com/c2c"
)
WEIXIN_LOGIN_TIMEOUT_SEC = _env_int("WEIXIN_LOGIN_TIMEOUT_SEC", 300)
WEIXIN_LOGIN_POLL_INTERVAL_SEC = _env_int("WEIXIN_LOGIN_POLL_INTERVAL_SEC", 3)
WEIXIN_TEXT_CHUNK_LIMIT = _env_int("WEIXIN_TEXT_CHUNK_LIMIT", 2000)
WEIXIN_DEBUG_UPDATES = os.getenv("WEIXIN_DEBUG_UPDATES", "false").lower() == "true"

# 日志配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# 模型配置路径
MODELS_CONFIG_PATH = os.getenv("MODELS_CONFIG_PATH", "config/models.json")


# ============================================================================
# OpenAI Client 初始化 - 从 models.json 获取配置
# ============================================================================

_clients_cache = {}
_wrapped_clients_cache = {}


def get_client_for_model(model_key: str | None = None, is_async: bool = True):
    """获取指定模型对应的 OpenAI 客户端"""
    if OpenAI is None or AsyncOpenAI is None:
        return None

    # Importing here to avoid circular dependencies
    from core.model_config import (
        get_api_key_for_model,
        get_base_url_for_model,
        get_current_model,
    )
    from core.llm_usage_store import wrap_openai_client

    key = model_key or get_current_model()
    api_key = get_api_key_for_model(key)
    base_url = get_base_url_for_model(key)

    if not api_key:
        return None

    cache_key = f"{api_key}:{base_url}:{is_async}"
    if cache_key not in _clients_cache:
        if is_async:
            _clients_cache[cache_key] = AsyncOpenAI(api_key=api_key, base_url=base_url)
        else:
            _clients_cache[cache_key] = OpenAI(api_key=api_key, base_url=base_url)

    wrapper_key = f"{cache_key}:{str(key or '').strip() or '__default__'}"
    if wrapper_key not in _wrapped_clients_cache:
        _wrapped_clients_cache[wrapper_key] = wrap_openai_client(
            _clients_cache[cache_key],
            default_model_key=str(key or "").strip(),
        )

    return _wrapped_clients_cache[wrapper_key]


# 为了兼容尚未迁移的旧代码，提供一个代理客户端
class AsyncOpenAIProxy:
    def __getattr__(self, name):
        client = get_client_for_model(None, True)
        if not client:
            raise RuntimeError("No client available for primary model")
        return getattr(client, name)


class SyncOpenAIProxy:
    def __getattr__(self, name):
        client = get_client_for_model(None, False)
        if not client:
            raise RuntimeError("No client available for primary model")
        return getattr(client, name)


openai_client = SyncOpenAIProxy() if OpenAI else None
openai_async_client = AsyncOpenAIProxy() if AsyncOpenAI else None


# ============================================================================
# 用户访问控制
# ============================================================================

ADMIN_USER_IDS_STR = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = set()
if ADMIN_USER_IDS_STR.strip():
    ADMIN_USER_IDS = {
        uid.strip() for uid in ADMIN_USER_IDS_STR.split(",") if uid.strip()
    }


async def is_user_allowed(user_id: int | str) -> bool:
    """
    检查用户是否有权限使用 Bot。

    管理员仅来自 `ADMIN_USER_IDS`；
    普通可用用户来自持久化 allow-list。
    """
    uid_str = str(user_id).strip()
    if not uid_str:
        return False
    if uid_str in ADMIN_USER_IDS:
        return True
    try:
        from core.state_store import check_user_allowed_in_db

        return await check_user_allowed_in_db(uid_str)
    except Exception:
        return False


def is_user_admin(user_id: int | str) -> bool:
    """检查用户是否为管理员"""
    return str(user_id).strip() in ADMIN_USER_IDS


# ============================================================================
# 下载配置
# ============================================================================


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

# External Search Service Provider ("searxng" is default)
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "searxng")
SEARXNG_URL = os.getenv("SEARXNG_URL")

# Server IP Override (Optional, for fixed deployment)
SERVER_IP = os.getenv("SERVER_IP")

# Deployment Staging Path (Optional, for Docker deployment feature)
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


def _as_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _as_float(value: str, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


# Auto recovery budget for terminal/recoverable failures in orchestrator loop
AUTO_RECOVERY_MAX_ATTEMPTS = int(os.getenv("AUTO_RECOVERY_MAX_ATTEMPTS", "3"))

# Core chat execution policy:
# - manager_only: manager handles the request directly
# - manager_with_subagents: manager may launch internal subagents when needed
CORE_CHAT_EXECUTION_MODE = (
    os.getenv("CORE_CHAT_EXECUTION_MODE", "manager_with_subagents").strip().lower()
    or "manager_with_subagents"
)

# Kernel-protected source roots (comma-separated absolute/relative paths)
KERNEL_PROTECTED_PATHS = os.getenv("KERNEL_PROTECTED_PATHS", "")

# 确保目录存在
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
