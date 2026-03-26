from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from core.audit_store import audit_store
from core.config import DATA_DIR


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


class RuntimeConfigStore:
    def __init__(self) -> None:
        self.path = (Path(DATA_DIR) / "kernel" / "runtime-config.json").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _default_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "auth": {
                "public_registration_enabled": False,
            },
            "cors": {
                "allowed_origins": [],
            },
            "platforms": {
                "telegram": True,
                "discord": True,
                "dingtalk": True,
                "weixin": True,
                "web": True,
            },
            "features": {
                "web_chat_uploads": True,
                "web_chat_tts": True,
                "admin_console": True,
            },
        }

    def read(self) -> dict[str, Any]:
        payload = self._default_payload()
        if not self.path.exists():
            return payload
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return payload
        if not isinstance(loaded, dict):
            return payload
        return _deep_merge(payload, loaded)

    def snapshot(
        self,
        payload: dict[str, Any],
        *,
        actor: str = "system",
        reason: str = "snapshot_runtime_config",
    ) -> dict[str, Any]:
        normalized = _deep_merge(self._default_payload(), payload or {})
        text = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
        audit_store.write_versioned(
            self.path,
            text,
            actor=actor,
            reason=reason,
            category="runtime_config",
        )
        return normalized

    def update_patch(
        self,
        patch: dict[str, Any],
        *,
        actor: str = "system",
        reason: str = "patch_runtime_config",
    ) -> dict[str, Any]:
        current = self.read()
        merged = _deep_merge(current, deepcopy(patch or {}))
        return self.snapshot(merged, actor=actor, reason=reason)

    def is_platform_enabled(self, platform: str, *, default: bool = True) -> bool:
        payload = self.read()
        platforms = payload.get("platforms")
        if not isinstance(platforms, dict):
            return default
        value = platforms.get(str(platform or "").strip().lower())
        if value is None:
            return default
        return bool(value)

    def get_public_registration_enabled(self) -> bool:
        payload = self.read()
        auth = payload.get("auth")
        if not isinstance(auth, dict):
            return False
        return bool(auth.get("public_registration_enabled", False))


runtime_config_store = RuntimeConfigStore()
