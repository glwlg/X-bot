from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any

from .base import now_iso, read_json, user_path, write_json

logger = logging.getLogger(__name__)


def _accounts_path(user_id: int | str):
    return user_path(user_id, "accounts.md")


async def _read_accounts(user_id: int | str) -> dict[str, dict[str, Any]]:
    payload = await read_json(_accounts_path(user_id), {})
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for service, item in payload.items():
        if not isinstance(item, dict):
            continue
        result[str(service)] = {
            "data": dict(item.get("data") or {}),
            "updated_at": str(item.get("updated_at") or now_iso()),
        }
    return result


async def add_account(user_id: int | str, service: str, data: Dict[str, Any]) -> bool:
    try:
        service_name = str(service or "").strip()
        if not service_name:
            return False
        payload = await _read_accounts(user_id)
        payload[service_name] = {
            "data": dict(data or {}),
            "updated_at": now_iso(),
        }
        await write_json(_accounts_path(user_id), payload)
        return True
    except Exception as e:
        logger.error(f"Error adding account: {e}")
        return False


async def get_account(user_id: int | str, service: str) -> Optional[Dict[str, Any]]:
    try:
        payload = await _read_accounts(user_id)
        item = payload.get(str(service or "").strip())
        if not isinstance(item, dict):
            return None
        data = item.get("data")
        return dict(data or {})
    except Exception as e:
        logger.error(f"Error getting account: {e}")
        return None


async def list_accounts(user_id: int | str) -> List[str]:
    try:
        payload = await _read_accounts(user_id)
        return sorted(payload.keys())
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        return []


async def delete_account(user_id: int | str, service: str) -> bool:
    try:
        payload = await _read_accounts(user_id)
        key = str(service or "").strip()
        if key not in payload:
            return False
        payload.pop(key, None)
        await write_json(_accounts_path(user_id), payload)
        return True
    except Exception as e:
        logger.error(f"Error deleting account: {e}")
        return False
