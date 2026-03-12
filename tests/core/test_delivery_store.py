from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from manager.relay.delivery_store import DeliveryJob, DeliveryStore


def _iso_before(*, minutes: int = 0, seconds: int = 0) -> str:
    return (
        datetime.now().astimezone() - timedelta(minutes=minutes, seconds=seconds)
    ).isoformat(timespec="seconds")


@pytest.mark.asyncio
async def test_delivery_health_reports_latency_and_oldest_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    store = DeliveryStore()
    store._loaded = True
    store._jobs = {
        "tsk-delivered": DeliveryJob(
            job_id="dlv-tsk-delivered",
            task_id="tsk-delivered",
            worker_id="worker-main",
            status="delivered",
            created_at=_iso_before(minutes=2),
            delivered_at=_iso_before(minutes=1, seconds=30),
            updated_at=_iso_before(minutes=1, seconds=30),
        ),
        "tsk-pending": DeliveryJob(
            job_id="dlv-tsk-pending",
            task_id="tsk-pending",
            worker_id="worker-main",
            status="retrying",
            created_at=_iso_before(minutes=3),
            updated_at=_iso_before(minutes=1),
        ),
    }

    health = await store.delivery_health(worker_id="worker-main")

    assert health["undelivered"] == 1
    assert health["retrying"] == 1
    assert health["delivered"] == 1
    assert float(health["avg_delivery_latency_sec"]) >= 29.0
    assert float(health["oldest_undelivered_age_sec"]) >= 179.0
