from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

PLATFORM_ALIASES: dict[str, str] = {}

EXPLICIT_METADATA_KEYS = (
    "proactive_delivery_target",
    "delivery_target",
)


def normalize_proactive_platform(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return PLATFORM_ALIASES.get(normalized, normalized)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _mapping_text(raw: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean_text(raw.get(key))
        if value:
            return value
    return ""


def _validate_owner_consistency(
    *,
    platform: str,
    owner_user_id: str,
    chat_id: str,
    allow_chat_id_mismatch: bool,
) -> None:
    if allow_chat_id_mismatch:
        return
    if platform != "telegram":
        return
    if not owner_user_id or not chat_id:
        return
    if owner_user_id != chat_id:
        raise ValueError("cross-user inconsistent target payload")


@dataclass(frozen=True)
class ProactiveDeliveryTarget:
    version: str
    owner_user_id: str
    platform: str
    chat_id: str

    @classmethod
    def from_legacy(
        cls,
        raw: Mapping[str, Any],
        *,
        expected_owner_user_id: str | None = None,
        allow_chat_id_mismatch: bool = False,
    ) -> "ProactiveDeliveryTarget":
        owner_user_id = _mapping_text(raw, "owner_user_id", "user_id")
        expected_owner = _clean_text(expected_owner_user_id)
        if expected_owner and owner_user_id and owner_user_id != expected_owner:
            raise ValueError("cross-user inconsistent target payload")
        if expected_owner:
            owner_user_id = expected_owner

        platform = normalize_proactive_platform(raw.get("platform"))
        chat_id = _mapping_text(raw, "chat_id", "platform_user_id")
        legacy_user_id = _mapping_text(raw, "user_id")
        explicit_owner_user_id = _mapping_text(raw, "owner_user_id")

        if (
            explicit_owner_user_id
            and legacy_user_id
            and explicit_owner_user_id != legacy_user_id
        ):
            raise ValueError("cross-user inconsistent target payload")
        if not owner_user_id:
            raise ValueError("owner_user_id is required")
        if not platform or platform == "heartbeat_daemon":
            raise ValueError("platform is required")
        if not chat_id:
            raise ValueError("chat_id is required")

        _validate_owner_consistency(
            platform=platform,
            owner_user_id=owner_user_id,
            chat_id=chat_id,
            allow_chat_id_mismatch=allow_chat_id_mismatch,
        )

        return cls(
            version="phase2",
            owner_user_id=owner_user_id,
            platform=platform,
            chat_id=chat_id,
        )

    @classmethod
    def from_user_default_binding(
        cls,
        *,
        owner_user_id: str,
        platform: str,
        chat_id: str,
    ) -> "ProactiveDeliveryTarget":
        normalized_owner = _clean_text(owner_user_id)
        normalized_platform = normalize_proactive_platform(platform)
        normalized_chat_id = _clean_text(chat_id)
        if not normalized_owner:
            raise ValueError("owner_user_id is required")
        if not normalized_platform or normalized_platform == "heartbeat_daemon":
            raise ValueError("platform is required")
        if not normalized_chat_id:
            raise ValueError("chat_id is required")
        return cls(
            version="phase2",
            owner_user_id=normalized_owner,
            platform=normalized_platform,
            chat_id=normalized_chat_id,
        )

    @classmethod
    def maybe_from_metadata(
        cls,
        metadata: Mapping[str, Any] | None,
        *,
        expected_owner_user_id: str | None = None,
    ) -> "ProactiveDeliveryTarget | None":
        if not isinstance(metadata, Mapping):
            return None
        for key in EXPLICIT_METADATA_KEYS:
            payload = metadata.get(key)
            if payload is None:
                continue
            if not isinstance(payload, Mapping):
                raise ValueError("invalid proactive delivery target payload")
            return cls.from_legacy(
                payload,
                expected_owner_user_id=expected_owner_user_id,
            )
        return None

    @classmethod
    def maybe_from_resource_binding(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        owner_user_id: str,
        platform: str,
    ) -> "ProactiveDeliveryTarget | None":
        if not isinstance(raw, Mapping):
            return None
        payload = raw.get("resource_binding") if isinstance(raw, Mapping) else None
        source = payload if isinstance(payload, Mapping) else raw
        chat_id = _mapping_text(source, "chat_id", "platform_user_id")
        if not chat_id:
            return None
        payload = dict(source)
        payload.setdefault("owner_user_id", owner_user_id)
        payload.setdefault("platform", platform)
        return cls.from_legacy(
            payload,
            expected_owner_user_id=owner_user_id,
            allow_chat_id_mismatch=True,
        )
