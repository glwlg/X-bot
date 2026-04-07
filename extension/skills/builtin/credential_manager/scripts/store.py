from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from core.storage_service import now_iso, storage_service, user_state_path

logger = logging.getLogger(__name__)


def _credentials_path(user_id: int | str):
    return user_state_path(user_id, "credential_manager", "credentials.md")


def _safe_service_name(service: str) -> str:
    return str(service or "").strip()


def _safe_entry_name(name: str, service: str) -> str:
    candidate = str(name or "").strip()
    return candidate or str(service or "").strip() or "default"


def _match_selector(entry: dict[str, Any], selector: str) -> bool:
    token = str(selector or "").strip().lower()
    if not token:
        return False
    for key in ("id", "name"):
        value = str(entry.get(key) or "").strip().lower()
        if value and value == token:
            return True
    return False


def _normalize_entry(
    service: str,
    raw_entry: dict[str, Any],
    *,
    fallback_id: str = "",
    fallback_name: str = "",
    fallback_updated_at: str = "",
) -> dict[str, Any] | None:
    if not isinstance(raw_entry, dict):
        return None

    raw_data = raw_entry.get("data")
    if not isinstance(raw_data, dict):
        return None

    entry_id = str(raw_entry.get("id") or fallback_id or uuid4().hex[:12]).strip()
    if not entry_id:
        entry_id = uuid4().hex[:12]

    updated_at = str(raw_entry.get("updated_at") or fallback_updated_at or now_iso()).strip()
    created_at = str(raw_entry.get("created_at") or updated_at or now_iso()).strip()
    name = _safe_entry_name(
        str(raw_entry.get("name") or fallback_name or ""),
        service,
    )

    return {
        "id": entry_id,
        "name": name,
        "data": dict(raw_data),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _normalize_service_record(service: str, raw_item: Any) -> dict[str, Any] | None:
    if not isinstance(raw_item, dict):
        return None

    legacy_entry = _normalize_entry(
        service,
        raw_item,
        fallback_id="default",
        fallback_name=service,
        fallback_updated_at=str(raw_item.get("updated_at") or now_iso()),
    )
    if legacy_entry is not None:
        return {
            "entries": [legacy_entry],
            "default_entry_id": legacy_entry["id"],
            "updated_at": legacy_entry["updated_at"],
        }

    raw_entries = raw_item.get("entries")
    if not isinstance(raw_entries, list):
        return None

    entries: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(raw_entries):
        normalized = _normalize_entry(
            service,
            raw_entry,
            fallback_id=f"entry_{index + 1}",
            fallback_name=service,
            fallback_updated_at=str(raw_item.get("updated_at") or now_iso()),
        )
        if normalized is None:
            continue
        entries.append(normalized)

    if not entries:
        return None

    default_entry_id = str(raw_item.get("default_entry_id") or "").strip()
    if not default_entry_id or not any(entry["id"] == default_entry_id for entry in entries):
        default_entry_id = entries[0]["id"]

    updated_at = str(raw_item.get("updated_at") or entries[0]["updated_at"] or now_iso()).strip()
    return {
        "entries": entries,
        "default_entry_id": default_entry_id,
        "updated_at": updated_at,
    }


async def _read_credentials(user_id: int | str) -> dict[str, dict[str, Any]]:
    payload = await storage_service.read(_credentials_path(user_id), {})
    if not isinstance(payload, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for service, item in payload.items():
        service_name = _safe_service_name(str(service or ""))
        if not service_name:
            continue
        normalized = _normalize_service_record(service_name, item)
        if normalized is None:
            continue
        result[service_name] = normalized
    return result


async def _write_credentials(user_id: int | str, payload: dict[str, dict[str, Any]]) -> None:
    await storage_service.write(_credentials_path(user_id), payload)


def _resolve_entry(
    item: dict[str, Any] | None,
    selector: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    entries = [entry for entry in list(item.get("entries") or []) if isinstance(entry, dict)]
    if not entries:
        return None

    if selector:
        for entry in entries:
            if _match_selector(entry, selector):
                return entry

    default_entry_id = str(item.get("default_entry_id") or "").strip()
    if default_entry_id:
        for entry in entries:
            if str(entry.get("id") or "").strip() == default_entry_id:
                return entry
    return entries[0]


async def add_credential(
    user_id: int | str,
    service: str,
    data: dict[str, Any],
) -> bool:
    try:
        service_name = _safe_service_name(service)
        if not service_name:
            return False

        payload = await _read_credentials(user_id)
        item = payload.get(service_name) or {
            "entries": [],
            "default_entry_id": "",
            "updated_at": now_iso(),
        }

        entries = [entry for entry in list(item.get("entries") or []) if isinstance(entry, dict)]
        updated_at = now_iso()
        replaced = False
        for entry in entries:
            if str(entry.get("id") or "").strip() == "default":
                entry["name"] = _safe_entry_name(str(entry.get("name") or "default"), service_name)
                entry["data"] = dict(data or {})
                entry["updated_at"] = updated_at
                replaced = True
                break

        if not replaced:
            entries.insert(
                0,
                {
                    "id": "default",
                    "name": _safe_entry_name("default", service_name),
                    "data": dict(data or {}),
                    "created_at": updated_at,
                    "updated_at": updated_at,
                },
            )

        item["entries"] = entries
        item["default_entry_id"] = "default"
        item["updated_at"] = updated_at
        payload[service_name] = item
        await _write_credentials(user_id, payload)
        return True
    except Exception as exc:
        logger.error("Error adding credential: %s", exc)
        return False


async def get_credential(
    user_id: int | str,
    service: str,
    selector: str | None = None,
) -> dict[str, Any] | None:
    try:
        payload = await _read_credentials(user_id)
        item = payload.get(_safe_service_name(service))
        entry = _resolve_entry(item, selector=selector)
        if not isinstance(entry, dict):
            return None
        return dict(entry.get("data") or {})
    except Exception as exc:
        logger.error("Error getting credential: %s", exc)
        return None


async def get_credential_entry(
    user_id: int | str,
    service: str,
    selector: str | None = None,
) -> dict[str, Any] | None:
    try:
        payload = await _read_credentials(user_id)
        item = payload.get(_safe_service_name(service))
        entry = _resolve_entry(item, selector=selector)
        if not isinstance(entry, dict):
            return None
        default_entry_id = str((item or {}).get("default_entry_id") or "").strip()
        return {
            "service": _safe_service_name(service),
            "id": str(entry.get("id") or "").strip(),
            "name": str(entry.get("name") or "").strip(),
            "data": dict(entry.get("data") or {}),
            "created_at": str(entry.get("created_at") or ""),
            "updated_at": str(entry.get("updated_at") or ""),
            "is_default": str(entry.get("id") or "").strip() == default_entry_id,
        }
    except Exception as exc:
        logger.error("Error getting credential entry: %s", exc)
        return None


async def list_credentials(user_id: int | str) -> list[str]:
    try:
        payload = await _read_credentials(user_id)
        return sorted(payload.keys())
    except Exception as exc:
        logger.error("Error listing credentials: %s", exc)
        return []


async def list_credential_entries(
    user_id: int | str,
    service: str,
) -> list[dict[str, Any]]:
    try:
        payload = await _read_credentials(user_id)
        item = payload.get(_safe_service_name(service))
        if not isinstance(item, dict):
            return []
        default_entry_id = str(item.get("default_entry_id") or "").strip()
        entries = [entry for entry in list(item.get("entries") or []) if isinstance(entry, dict)]
        return [
            {
                "service": _safe_service_name(service),
                "id": str(entry.get("id") or "").strip(),
                "name": str(entry.get("name") or "").strip(),
                "data": dict(entry.get("data") or {}),
                "created_at": str(entry.get("created_at") or ""),
                "updated_at": str(entry.get("updated_at") or ""),
                "is_default": str(entry.get("id") or "").strip() == default_entry_id,
            }
            for entry in entries
        ]
    except Exception as exc:
        logger.error("Error listing credential entries: %s", exc)
        return []


async def list_credentials_detailed(user_id: int | str) -> list[dict[str, Any]]:
    try:
        payload = await _read_credentials(user_id)
        services: list[dict[str, Any]] = []
        for service in sorted(payload.keys()):
            item = payload.get(service) or {}
            services.append(
                {
                    "service": service,
                    "default_entry_id": str(item.get("default_entry_id") or "").strip(),
                    "entries": await list_credential_entries(user_id, service),
                }
            )
        return services
    except Exception as exc:
        logger.error("Error listing detailed credentials: %s", exc)
        return []


async def upsert_credential_entry(
    user_id: int | str,
    service: str,
    *,
    name: str,
    data: dict[str, Any],
    credential_id: str | None = None,
    set_default: bool = False,
) -> dict[str, Any] | None:
    try:
        service_name = _safe_service_name(service)
        if not service_name:
            return None

        payload = await _read_credentials(user_id)
        item = payload.get(service_name) or {
            "entries": [],
            "default_entry_id": "",
            "updated_at": now_iso(),
        }
        entries = [entry for entry in list(item.get("entries") or []) if isinstance(entry, dict)]
        updated_at = now_iso()
        entry_id = str(credential_id or "").strip()
        entry_name = _safe_entry_name(name, service_name)

        target: dict[str, Any] | None = None
        if entry_id:
            for entry in entries:
                if str(entry.get("id") or "").strip() == entry_id:
                    target = entry
                    break
        if target is None:
            target = {
                "id": entry_id or uuid4().hex[:12],
                "name": entry_name,
                "data": {},
                "created_at": updated_at,
                "updated_at": updated_at,
            }
            entries.append(target)

        target["name"] = entry_name
        target["data"] = dict(data or {})
        target["updated_at"] = updated_at
        if not str(target.get("created_at") or "").strip():
            target["created_at"] = updated_at

        item["entries"] = entries
        if set_default or not str(item.get("default_entry_id") or "").strip():
            item["default_entry_id"] = str(target.get("id") or "").strip()
        item["updated_at"] = updated_at
        payload[service_name] = item
        await _write_credentials(user_id, payload)
        return await get_credential_entry(user_id, service_name, str(target.get("id") or ""))
    except Exception as exc:
        logger.error("Error upserting credential entry: %s", exc)
        return None


async def set_default_credential_entry(
    user_id: int | str,
    service: str,
    credential_id: str,
) -> dict[str, Any] | None:
    try:
        service_name = _safe_service_name(service)
        payload = await _read_credentials(user_id)
        item = payload.get(service_name)
        if not isinstance(item, dict):
            return None

        target_id = str(credential_id or "").strip()
        if not any(str(entry.get("id") or "").strip() == target_id for entry in list(item.get("entries") or [])):
            return None

        item["default_entry_id"] = target_id
        item["updated_at"] = now_iso()
        payload[service_name] = item
        await _write_credentials(user_id, payload)
        return await get_credential_entry(user_id, service_name, target_id)
    except Exception as exc:
        logger.error("Error setting default credential entry: %s", exc)
        return None


async def delete_credential(user_id: int | str, service: str) -> bool:
    try:
        payload = await _read_credentials(user_id)
        key = _safe_service_name(service)
        if key not in payload:
            return False
        payload.pop(key, None)
        await _write_credentials(user_id, payload)
        return True
    except Exception as exc:
        logger.error("Error deleting credential: %s", exc)
        return False


async def delete_credential_entry(
    user_id: int | str,
    service: str,
    credential_id: str,
) -> bool:
    try:
        service_name = _safe_service_name(service)
        payload = await _read_credentials(user_id)
        item = payload.get(service_name)
        if not isinstance(item, dict):
            return False

        target_id = str(credential_id or "").strip()
        entries = [
            entry
            for entry in list(item.get("entries") or [])
            if isinstance(entry, dict) and str(entry.get("id") or "").strip() != target_id
        ]
        if len(entries) == len(list(item.get("entries") or [])):
            return False

        if not entries:
            payload.pop(service_name, None)
        else:
            item["entries"] = entries
            if str(item.get("default_entry_id") or "").strip() == target_id:
                item["default_entry_id"] = str(entries[0].get("id") or "").strip()
            item["updated_at"] = now_iso()
            payload[service_name] = item

        await _write_credentials(user_id, payload)
        return True
    except Exception as exc:
        logger.error("Error deleting credential entry: %s", exc)
        return False


__all__ = [
    "add_credential",
    "delete_credential",
    "delete_credential_entry",
    "get_credential",
    "get_credential_entry",
    "list_credential_entries",
    "list_credentials",
    "list_credentials_detailed",
    "set_default_credential_entry",
    "upsert_credential_entry",
]
