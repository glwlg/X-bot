import logging

from core.state_io import read_json, write_json
from core.state_paths import user_path

logger = logging.getLogger(__name__)


def _accounting_state_path(user_id: int | str):
    return user_path(user_id, "accounting", "state.md")


async def get_active_book_id(user_id: int | str) -> int | None:
    try:
        data = await read_json(_accounting_state_path(user_id), {})
        book_id = data.get("active_book_id")
        return int(book_id) if book_id else None
    except Exception as e:
        logger.error(f"Error getting active book id: {e}")
        return None


async def set_active_book_id(user_id: int | str, book_id: int) -> bool:
    try:
        data = await read_json(_accounting_state_path(user_id), {})
        data["active_book_id"] = book_id
        await write_json(_accounting_state_path(user_id), data)
        return True
    except Exception as e:
        logger.error(f"Error setting active book id: {e}")
        return False
