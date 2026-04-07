from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from core.app_paths import env_path
from core.audit_store import audit_store


ENV_PATH = env_path()
ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

MANAGED_ENV_DEFAULTS: dict[str, str] = {
    "ADMIN_USER_IDS": "",
    "TELEGRAM_BOT_TOKEN": "",
    "DISCORD_BOT_TOKEN": "",
    "DINGTALK_CLIENT_ID": "",
    "DINGTALK_CLIENT_SECRET": "",
    "WEIXIN_BASE_URL": "https://ilinkai.weixin.qq.com/",
    "WEIXIN_CDN_BASE_URL": "https://novac2c.cdn.weixin.qq.com/c2c",
}


def _normalize_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _parse_existing_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    payload = dotenv_values(str(ENV_PATH))
    result: dict[str, str] = {}
    for key, value in payload.items():
        if not key:
            continue
        result[str(key)] = "" if value is None else str(value)
    return result


def read_managed_env() -> dict[str, str]:
    file_values = _parse_existing_env()
    result: dict[str, str] = {}
    for key, default in MANAGED_ENV_DEFAULTS.items():
        if key in file_values:
            result[key] = file_values[key]
            continue
        result[key] = str(os.getenv(key, default))
    return result


def env_bool(value: Any, *, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def env_csv_list(value: Any) -> list[str]:
    raw = str(value or "").replace("\n", ",")
    items = [item.strip() for item in raw.split(",") if item.strip()]
    deduped: list[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _apply_process_env(updates: dict[str, str]) -> None:
    for key, value in updates.items():
        os.environ[key] = value
    try:
        import core.config as config_module

        if "TELEGRAM_BOT_TOKEN" in updates:
            config_module.TELEGRAM_BOT_TOKEN = updates["TELEGRAM_BOT_TOKEN"]
        if "DISCORD_BOT_TOKEN" in updates:
            config_module.DISCORD_BOT_TOKEN = updates["DISCORD_BOT_TOKEN"]
        if "DINGTALK_CLIENT_ID" in updates:
            config_module.DINGTALK_CLIENT_ID = updates["DINGTALK_CLIENT_ID"]
        if "DINGTALK_CLIENT_SECRET" in updates:
            config_module.DINGTALK_CLIENT_SECRET = updates["DINGTALK_CLIENT_SECRET"]
        if "WEIXIN_BASE_URL" in updates:
            config_module.WEIXIN_BASE_URL = updates["WEIXIN_BASE_URL"]
        if "WEIXIN_CDN_BASE_URL" in updates:
            config_module.WEIXIN_CDN_BASE_URL = updates["WEIXIN_CDN_BASE_URL"]
        if "ADMIN_USER_IDS" in updates:
            config_module.ADMIN_USER_IDS_STR = updates["ADMIN_USER_IDS"]
            config_module.ADMIN_USER_IDS = set(env_csv_list(updates["ADMIN_USER_IDS"]))
    except Exception:
        return


def write_managed_env(
    updates: dict[str, Any],
    *,
    actor: str = "system",
    reason: str = "update_env",
) -> dict[str, Any]:
    normalized_updates = {
        key: _normalize_env_value(value)
        for key, value in dict(updates or {}).items()
        if str(key or "").strip()
    }
    if not normalized_updates:
        return {
            "path": str(ENV_PATH),
            "changed_keys": [],
        }

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    rendered_lines: list[str] = []
    seen: set[str] = set()

    for line in lines:
        match = ENV_LINE_RE.match(line)
        if not match:
            rendered_lines.append(line)
            continue
        key = match.group(1)
        if key in normalized_updates:
            rendered_lines.append(f"{key}={json.dumps(normalized_updates[key], ensure_ascii=False)}")
            seen.add(key)
        else:
            rendered_lines.append(line)

    missing_keys = [key for key in normalized_updates if key not in seen]
    if missing_keys and rendered_lines and rendered_lines[-1].strip():
        rendered_lines.append("")
    for key in missing_keys:
        rendered_lines.append(f"{key}={json.dumps(normalized_updates[key], ensure_ascii=False)}")

    rendered = "\n".join(rendered_lines).rstrip() + "\n"
    result = audit_store.write_versioned(
        ENV_PATH,
        rendered,
        actor=actor,
        reason=reason,
        category="env_config",
    )
    _apply_process_env(normalized_updates)
    return {
        "path": str(ENV_PATH),
        "changed_keys": sorted(normalized_updates.keys()),
        "previous_version_id": str(result.get("previous_version_id", "")),
    }


def ensure_admin_user_id_present(
    user_id: int | str,
    *,
    actor: str = "system",
    reason: str = "ensure_admin_user_id",
) -> list[str]:
    admin_id = str(user_id or "").strip()
    if not admin_id:
        return env_csv_list(read_managed_env().get("ADMIN_USER_IDS", ""))
    current_ids = env_csv_list(read_managed_env().get("ADMIN_USER_IDS", ""))
    if admin_id in current_ids:
        return current_ids
    current_ids.append(admin_id)
    write_managed_env(
        {"ADMIN_USER_IDS": ",".join(current_ids)},
        actor=actor,
        reason=reason,
    )
    return current_ids
