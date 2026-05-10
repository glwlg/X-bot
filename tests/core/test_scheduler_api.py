from types import SimpleNamespace

import pytest

from api.api.endpoints import scheduler as scheduler_endpoint


@pytest.mark.asyncio
async def test_scheduler_list_endpoint_returns_paused_tasks(monkeypatch):
    async def fake_get_primary_platform_user_id(user_id, session):
        assert user_id == 42
        assert session == "session"
        return "telegram-user"

    async def fake_get_all_scheduled_tasks(user_id):
        assert user_id == "telegram-user"
        return [
            {
                "id": 1,
                "crontab": "0 8 * * *",
                "instruction": "paused task",
                "is_active": False,
            }
        ]

    monkeypatch.setattr(
        scheduler_endpoint,
        "get_primary_platform_user_id",
        fake_get_primary_platform_user_id,
    )
    monkeypatch.setattr(
        scheduler_endpoint.scheduler_store,
        "get_all_scheduled_tasks",
        fake_get_all_scheduled_tasks,
    )

    result = await scheduler_endpoint.get_tasks(
        current_user=SimpleNamespace(id=42),
        session="session",
    )

    assert result == [
        {
            "id": 1,
            "crontab": "0 8 * * *",
            "instruction": "paused task",
            "is_active": False,
        }
    ]
