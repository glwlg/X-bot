import sys
import types

import pytest

import main


@pytest.mark.asyncio
async def test_init_services_starts_rss_stock_and_dynamic_schedulers(monkeypatch):
    calls: list[str] = []

    async def fake_init_db():
        calls.append("init_db")

    async def fake_load_jobs():
        calls.append("load_jobs")

    def fake_scheduler_start():
        calls.append("scheduler.start")

    def fake_start_rss_scheduler():
        calls.append("start_rss_scheduler")

    def fake_start_stock_scheduler():
        calls.append("start_stock_scheduler")

    def fake_start_dynamic_skill_scheduler():
        calls.append("start_dynamic_skill_scheduler")

    def fake_scan_skills():
        calls.append("scan_skills")
        return {"rss_subscribe": {}, "stock_watch": {}}

    def fake_snapshot(*_args, **_kwargs):
        calls.append("snapshot")

    monkeypatch.setitem(
        sys.modules,
        "core.state_store",
        types.SimpleNamespace(init_db=fake_init_db),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.scheduler",
        types.SimpleNamespace(
            scheduler=types.SimpleNamespace(start=fake_scheduler_start),
            load_jobs_from_db=fake_load_jobs,
            start_rss_scheduler=fake_start_rss_scheduler,
            start_stock_scheduler=fake_start_stock_scheduler,
            start_dynamic_skill_scheduler=fake_start_dynamic_skill_scheduler,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.skill_loader",
        types.SimpleNamespace(
            skill_loader=types.SimpleNamespace(
                scan_skills=fake_scan_skills,
                get_skill_index=lambda: {"rss_subscribe": {}, "stock_watch": {}},
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.kernel_config_store",
        types.SimpleNamespace(
            kernel_config_store=types.SimpleNamespace(snapshot=fake_snapshot)
        ),
    )

    await main.init_services()

    assert "init_db" in calls
    assert "scheduler.start" in calls
    assert "load_jobs" in calls
    assert "start_rss_scheduler" in calls
    assert "start_stock_scheduler" in calls
    assert "start_dynamic_skill_scheduler" in calls
    assert "scan_skills" in calls
    assert "snapshot" in calls
