from __future__ import annotations

from typing import Any, Mapping

from core.heartbeat_store import heartbeat_store
from shared.contracts.proactive_delivery_target import (
    ProactiveDeliveryTarget,
    normalize_proactive_platform,
)


async def load_resource_delivery_binding(
    *,
    owner_user_id: str,
    platform: str,
    metadata: Mapping[str, Any] | None = None,
) -> ProactiveDeliveryTarget | None:
    return ProactiveDeliveryTarget.maybe_from_resource_binding(
        metadata,
        owner_user_id=owner_user_id,
        platform=platform,
    )


async def load_user_default_binding(
    *,
    owner_user_id: str,
    platform: str,
) -> ProactiveDeliveryTarget | None:
    normalized_owner = str(owner_user_id or "").strip()
    normalized_platform = normalize_proactive_platform(platform)
    if (
        not normalized_owner
        or not normalized_platform
        or normalized_platform == "heartbeat_daemon"
    ):
        return None

    delivery = await heartbeat_store.get_delivery_target(normalized_owner)
    target_platform = normalize_proactive_platform(delivery.get("platform", ""))
    target_chat_id = str(delivery.get("chat_id") or "").strip()
    if not target_platform or not target_chat_id:
        return None
    if target_platform != normalized_platform:
        return None

    return ProactiveDeliveryTarget.from_user_default_binding(
        owner_user_id=normalized_owner,
        platform=target_platform,
        chat_id=target_chat_id,
    )


async def resolve_proactive_target(
    *,
    owner_user_id: str,
    platform: str,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    normalized_owner = str(owner_user_id or "").strip()
    normalized_platform = normalize_proactive_platform(platform)
    if (
        not normalized_owner
        or not normalized_platform
        or normalized_platform == "heartbeat_daemon"
    ):
        return "", ""

    explicit = ProactiveDeliveryTarget.maybe_from_metadata(
        metadata,
        expected_owner_user_id=normalized_owner,
    )
    if explicit is not None:
        return explicit.platform, explicit.chat_id

    resource_binding = await load_resource_delivery_binding(
        owner_user_id=normalized_owner,
        platform=normalized_platform,
        metadata=metadata,
    )
    if resource_binding is not None:
        return resource_binding.platform, resource_binding.chat_id

    default_binding = await load_user_default_binding(
        owner_user_id=normalized_owner,
        platform=normalized_platform,
    )
    if default_binding is not None:
        return default_binding.platform, default_binding.chat_id

    return "", ""
