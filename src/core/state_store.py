"""Centralized state store API.

This module is the app-facing storage surface and delegates to
filesystem-backed storage implementations in `repositories/*`.
"""

from repositories.base import init_db
from repositories.cache_repo import save_video_cache, get_video_cache
from repositories.reminder_repo import (
    add_reminder,
    delete_reminder,
    get_pending_reminders,
)
from repositories.subscription_repo import (
    add_subscription,
    delete_subscription,
    delete_subscription_by_id,
    get_user_subscriptions,
    get_all_subscriptions,
    update_subscription_status,
)
from repositories.user_settings_repo import set_translation_mode, get_user_settings
from repositories.allowed_users_repo import (
    add_allowed_user,
    remove_allowed_user,
    get_allowed_users,
    check_user_allowed_in_db,
)
from repositories.watchlist_repo import (
    add_watchlist_stock,
    remove_watchlist_stock,
    get_user_watchlist,
    get_all_watchlist_users,
)
from repositories.chat_repo import (
    save_message,
    get_session_messages,
    get_latest_session_id,
    search_messages,
    get_recent_messages_for_user,
    get_day_session_transcripts,
)
from repositories.task_repo import (
    add_scheduled_task,
    get_all_active_tasks,
    update_task_status,
    delete_task,
)
from repositories.account_repo import (
    add_account,
    get_account,
    list_accounts,
    delete_account,
)
