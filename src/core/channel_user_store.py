from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict
from urllib.parse import quote

import yaml

from core.config import DATA_DIR


DEFAULT_ACCESS: Dict[str, bool] = {
    "chat": True,
    "rss": False,
    "heartbeat": False,
    "scheduler": False,
    "stock": False,
    "accounting": False,
}


DEFAULT_USER_MD = """# USER

## 用户身份
- 这是当前渠道用户的独立身份描述文件。

## 称呼偏好
- 默认使用自然、中性的称呼。
- 如用户明确指定称呼或关系，以用户当前要求为准。
"""


@dataclass(frozen=True)
class ChannelUserProfile:
    platform: str
    platform_user_id: str
    status: str
    role: str
    is_admin: bool
    access: Dict[str, bool]
    user_md_path: str


class ChannelUserStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._ensure_file()

    @property
    def path(self) -> Path:
        return (
            Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()
            / "system"
            / "channel_users.yaml"
        ).resolve()

    @staticmethod
    def _safe_text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _safe_part(value: Any, fallback: str = "unknown") -> str:
        raw = str(value or "").strip()
        if not raw:
            return fallback
        safe = quote(raw, safe="._-:")
        return safe or fallback

    def _default_payload(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "defaults": {
                "access": dict(DEFAULT_ACCESS),
            },
            "platforms": {},
        }

    def _ensure_file(self) -> None:
        with self._lock:
            if self.path.exists():
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write_unlocked(self._default_payload())

    def _read_unlocked(self) -> Dict[str, Any]:
        default = self._default_payload()
        if not self.path.exists():
            return default
        try:
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception:
            loaded = {}
        if not isinstance(loaded, dict):
            return default
        merged = dict(default)
        merged.update(loaded)
        defaults = dict(default.get("defaults") or {})
        defaults.update(dict(loaded.get("defaults") or {}))
        defaults["access"] = self._merge_access(
            dict(default.get("defaults", {}).get("access") or {}),
            dict(defaults.get("access") or {}),
        )
        merged["defaults"] = defaults
        platforms = loaded.get("platforms")
        merged["platforms"] = dict(platforms) if isinstance(platforms, dict) else {}
        return merged

    def _write_unlocked(self, payload: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
        )
        self.path.write_text(text, encoding="utf-8")

    def _default_user_md_path(self, platform: str, platform_user_id: str) -> Path:
        safe_platform = self._safe_part(platform, "platform")
        safe_user_id = self._safe_part(platform_user_id, "user")
        return (
            Path(os.getenv("DATA_DIR", DATA_DIR)).resolve()
            / "userland"
            / "channel-users"
            / safe_platform
            / safe_user_id
            / "USER.md"
        ).resolve()

    def _merge_access(
        self,
        base: Dict[str, Any] | None,
        override: Dict[str, Any] | None,
    ) -> Dict[str, bool]:
        merged = dict(DEFAULT_ACCESS)
        for source in (base or {}, override or {}):
            for key, value in dict(source or {}).items():
                token = self._safe_text(key).lower()
                if token in DEFAULT_ACCESS:
                    merged[token] = bool(value)
        return merged

    def _platform_bucket(
        self,
        payload: Dict[str, Any],
        platform: str,
        *,
        create: bool = False,
    ) -> Dict[str, Any] | None:
        platforms = payload.setdefault("platforms", {})
        if not isinstance(platforms, dict):
            if not create:
                return None
            platforms = {}
            payload["platforms"] = platforms
        safe_platform = self._safe_text(platform).lower()
        current = platforms.get(safe_platform)
        if isinstance(current, dict):
            current.setdefault("users", {})
            return current
        if not create:
            return None
        created = {"users": {}}
        platforms[safe_platform] = created
        return created

    def _user_entry(
        self,
        payload: Dict[str, Any],
        platform: str,
        platform_user_id: str,
        *,
        create: bool = False,
    ) -> Dict[str, Any] | None:
        bucket = self._platform_bucket(payload, platform, create=create)
        if not isinstance(bucket, dict):
            return None
        users = bucket.setdefault("users", {})
        if not isinstance(users, dict):
            if not create:
                return None
            users = {}
            bucket["users"] = users
        safe_user_id = self._safe_text(platform_user_id)
        current = users.get(safe_user_id)
        if isinstance(current, dict):
            return current
        if not create:
            return None
        current = {}
        users[safe_user_id] = current
        return current

    def _ensure_user_md_file(self, path: Path, *, content: str = DEFAULT_USER_MD) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")

    def ensure_user(
        self,
        *,
        platform: str,
        platform_user_id: str,
        role: str = "member",
        access: Dict[str, bool] | None = None,
        status: str = "active",
        user_md_path: str | None = None,
    ) -> ChannelUserProfile:
        safe_platform = self._safe_text(platform).lower()
        safe_user_id = self._safe_text(platform_user_id)
        if not safe_platform or not safe_user_id:
            raise ValueError("platform and platform_user_id are required")

        with self._lock:
            payload = self._read_unlocked()
            defaults = dict(payload.get("defaults") or {})
            default_access = dict(defaults.get("access") or {})
            entry = self._user_entry(
                payload,
                safe_platform,
                safe_user_id,
                create=True,
            )
            assert entry is not None
            entry["status"] = self._safe_text(entry.get("status") or status) or "active"
            entry["role"] = self._safe_text(entry.get("role") or role) or "member"

            resolved_user_md_path = self._safe_text(entry.get("user_md_path") or user_md_path)
            if not resolved_user_md_path:
                resolved_user_md_path = str(
                    self._default_user_md_path(safe_platform, safe_user_id)
                )
            entry["user_md_path"] = resolved_user_md_path
            entry["access"] = self._merge_access(
                default_access,
                dict(entry.get("access") or access or {}),
            )
            self._write_unlocked(payload)

        self._ensure_user_md_file(Path(resolved_user_md_path))
        return self.get_profile(
            platform=safe_platform,
            platform_user_id=safe_user_id,
            is_admin=False,
        )

    def get_profile(
        self,
        *,
        platform: str,
        platform_user_id: str,
        is_admin: bool,
    ) -> ChannelUserProfile:
        safe_platform = self._safe_text(platform).lower()
        safe_user_id = self._safe_text(platform_user_id)
        if not safe_platform or not safe_user_id:
            return ChannelUserProfile(
                platform=safe_platform,
                platform_user_id=safe_user_id,
                status="inactive",
                role="member",
                is_admin=bool(is_admin),
                access=dict(DEFAULT_ACCESS),
                user_md_path="",
            )

        if is_admin:
            return ChannelUserProfile(
                platform=safe_platform,
                platform_user_id=safe_user_id,
                status="active",
                role="admin",
                is_admin=True,
                access={key: True for key in DEFAULT_ACCESS},
                user_md_path=str(
                    (Path(os.getenv("DATA_DIR", DATA_DIR)).resolve() / "USER.md").resolve()
                ),
            )

        with self._lock:
            payload = self._read_unlocked()
            defaults = dict(payload.get("defaults") or {})
            default_access = dict(defaults.get("access") or {})
            entry = self._user_entry(
                payload,
                safe_platform,
                safe_user_id,
                create=False,
            )
            if not isinstance(entry, dict):
                resolved_path = str(
                    self._default_user_md_path(safe_platform, safe_user_id)
                )
                access = self._merge_access(default_access, {})
                return ChannelUserProfile(
                    platform=safe_platform,
                    platform_user_id=safe_user_id,
                    status="active",
                    role="member",
                    is_admin=False,
                    access=access,
                    user_md_path=resolved_path,
                )

            resolved_path = self._safe_text(entry.get("user_md_path"))
            if not resolved_path:
                resolved_path = str(
                    self._default_user_md_path(safe_platform, safe_user_id)
                )
            access = self._merge_access(default_access, dict(entry.get("access") or {}))
            return ChannelUserProfile(
                platform=safe_platform,
                platform_user_id=safe_user_id,
                status=self._safe_text(entry.get("status") or "active") or "active",
                role=self._safe_text(entry.get("role") or "member") or "member",
                is_admin=False,
                access=access,
                user_md_path=resolved_path,
            )

    def is_feature_enabled(
        self,
        *,
        platform: str,
        platform_user_id: str,
        feature: str,
        is_admin: bool,
    ) -> bool:
        safe_feature = self._safe_text(feature).lower()
        if safe_feature not in DEFAULT_ACCESS:
            return False
        profile = self.get_profile(
            platform=platform,
            platform_user_id=platform_user_id,
            is_admin=is_admin,
        )
        if profile.is_admin:
            return True
        if profile.status != "active":
            return False
        return bool(profile.access.get(safe_feature))

    def load_user_md(
        self,
        *,
        platform: str,
        platform_user_id: str,
        is_admin: bool,
    ) -> str:
        profile = self.get_profile(
            platform=platform,
            platform_user_id=platform_user_id,
            is_admin=is_admin,
        )
        path = Path(profile.user_md_path).resolve() if profile.user_md_path else None
        if path is None:
            return ""
        self._ensure_user_md_file(path)
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""


channel_user_store = ChannelUserStore()
