"""Statistics feature retired; keep compatibility no-op APIs."""


async def increment_stat(user_id: int | str, stat_name: str, count: int = 1) -> None:
    del user_id, stat_name, count
    return None


async def get_user_stats_text(user_id: int | str) -> str:
    del user_id
    return "📊 统计功能已下线。"


def get_global_stats_text() -> str:
    return "📊 统计功能已下线。"
