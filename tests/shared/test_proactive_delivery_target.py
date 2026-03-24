import pytest

from shared.contracts.proactive_delivery_target import ProactiveDeliveryTarget


def test_proactive_delivery_target_accepts_explicit_platform():
    target = ProactiveDeliveryTarget.from_legacy(
        {
            "platform": "telegram",
            "chat_id": "257675041",
            "user_id": "257675041",
        }
    )

    assert target.platform == "telegram"
    assert target.chat_id == "257675041"
    assert target.owner_user_id == "257675041"


def test_proactive_delivery_target_rejects_cross_user_mismatch():
    with pytest.raises(ValueError, match="cross-user"):
        ProactiveDeliveryTarget.from_legacy(
            {
                "platform": "telegram",
                "chat_id": "2002",
                "user_id": "1001",
            }
        )


def test_proactive_delivery_target_rejects_telegram_chat_id_without_owner_fields():
    with pytest.raises(ValueError, match="cross-user"):
        ProactiveDeliveryTarget.from_legacy(
            {
                "platform": "telegram",
                "chat_id": "2002",
            },
            expected_owner_user_id="1001",
        )


def test_proactive_delivery_target_loads_nested_metadata_target():
    target = ProactiveDeliveryTarget.maybe_from_metadata(
        {
            "proactive_delivery_target": {
                "platform": "telegram",
                "chat_id": "257675041",
                "user_id": "257675041",
            }
        }
    )

    assert target is not None
    assert target.platform == "telegram"
    assert target.chat_id == "257675041"


def test_proactive_delivery_target_ignores_metadata_without_target_fields():
    target = ProactiveDeliveryTarget.maybe_from_metadata({"session_id": "cron-1"})

    assert target is None


def test_proactive_delivery_target_rejects_explicit_metadata_without_owner_fields():
    with pytest.raises(ValueError, match="cross-user"):
        ProactiveDeliveryTarget.maybe_from_metadata(
            {
                "proactive_delivery_target": {
                    "platform": "telegram",
                    "chat_id": "2002",
                }
            },
            expected_owner_user_id="1001",
        )


def test_proactive_delivery_target_top_level_chat_id_is_not_treated_as_explicit_target():
    target = ProactiveDeliveryTarget.maybe_from_metadata(
        {
            "platform": "telegram",
            "chat_id": "resource-chat",
            "user_id": "257675041",
        },
        expected_owner_user_id="257675041",
    )

    assert target is None


def test_proactive_delivery_target_loads_nested_resource_binding():
    target = ProactiveDeliveryTarget.maybe_from_resource_binding(
        {
            "resource_binding": {
                "platform": "telegram",
                "chat_id": "resource-chat",
            }
        },
        owner_user_id="257675041",
        platform="telegram",
    )

    assert target is not None
    assert target.platform == "telegram"
    assert target.chat_id == "resource-chat"
