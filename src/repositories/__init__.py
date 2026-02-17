"""Repositories 模块 - 数据访问层。"""

from .base import init_db
from .cache_repo import save_video_cache, get_video_cache
from .reminder_repo import add_reminder, delete_reminder, get_pending_reminders
from .subscription_repo import (
    add_subscription,
    delete_subscription,
    delete_subscription_by_id,
    get_user_subscriptions,
    get_all_subscriptions,
    update_subscription_status,
)
from .user_settings_repo import set_translation_mode, get_user_settings
from .allowed_users_repo import (
    add_allowed_user,
    remove_allowed_user,
    get_allowed_users,
    check_user_allowed_in_db,
)
from .watchlist_repo import (
    add_watchlist_stock,
    remove_watchlist_stock,
    get_user_watchlist,
    get_all_watchlist_users,
)
from .chat_repo import (
    save_message,
    get_session_messages,
    get_latest_session_id,
    search_messages,
    get_recent_messages_for_user,
)
