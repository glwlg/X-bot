import sys
import types

import pytest

import main


@pytest.mark.asyncio
async def test_init_services_starts_extension_runtime(monkeypatch):
    calls: list[str] = []

    async def fake_init_db():
        calls.append("init_db")

    async def fake_load_jobs():
        calls.append("load_jobs")

    def fake_scheduler_start():
        calls.append("scheduler.start")

    def fake_start_dynamic_skill_scheduler():
        calls.append("start_dynamic_skill_scheduler")

    def fake_scan_skills():
        calls.append("scan_skills")
        return {"rss_subscribe": {}, "stock_watch": {}}

    def fake_activate_memory(_runtime):
        calls.append("memory_registry.activate_extension")

    def fake_register_channels(_runtime):
        calls.append("channel_registry.register_extensions")

    def fake_register_skills(_runtime):
        calls.append("skill_registry.register_extensions")

    def fake_register_plugins(_runtime):
        calls.append("plugin_registry.register_extensions")

    def fake_snapshot(*_args, **_kwargs):
        calls.append("snapshot")

    async def fake_memory_initialize():
        calls.append("long_term_memory.initialize")

    async def fake_compact_storage():
        calls.append("task_inbox.compact_storage")

    def fake_audit_maintain():
        calls.append("audit_store.maintain")

    fake_runtime = types.SimpleNamespace(run_startup=lambda: None, run_shutdown=lambda: None)

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
            start_dynamic_skill_scheduler=fake_start_dynamic_skill_scheduler,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.kernel_config_store",
        types.SimpleNamespace(
            kernel_config_store=types.SimpleNamespace(snapshot=fake_snapshot)
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.task_inbox",
        types.SimpleNamespace(task_inbox=types.SimpleNamespace(compact_storage=fake_compact_storage)),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.audit_store",
        types.SimpleNamespace(audit_store=types.SimpleNamespace(maintain=fake_audit_maintain)),
    )
    monkeypatch.setattr(
        main.long_term_memory,
        "initialize",
        fake_memory_initialize,
    )
    monkeypatch.setattr(
        main.long_term_memory,
        "get_provider_name",
        lambda: "file",
    )
    monkeypatch.setattr(main, "init_extension_runtime", lambda **_kwargs: fake_runtime)
    monkeypatch.setattr(main.memory_registry, "activate_extension", fake_activate_memory)
    monkeypatch.setattr(main.channel_registry, "register_extensions", fake_register_channels)
    monkeypatch.setattr(main.skill_registry, "scan_skills", fake_scan_skills)
    monkeypatch.setattr(
        main.skill_registry,
        "get_skill_index",
        lambda: {"rss_subscribe": {}, "stock_watch": {}},
    )
    monkeypatch.setattr(main.skill_registry, "register_extensions", fake_register_skills)
    monkeypatch.setattr(main.plugin_registry, "register_extensions", fake_register_plugins)

    await main.init_services()

    assert "init_db" in calls
    assert "scheduler.start" in calls
    assert "load_jobs" in calls
    assert "start_dynamic_skill_scheduler" in calls
    assert "memory_registry.activate_extension" in calls
    assert "channel_registry.register_extensions" in calls
    assert "skill_registry.register_extensions" in calls
    assert "plugin_registry.register_extensions" in calls
    assert "scan_skills" in calls
    assert "long_term_memory.initialize" in calls
    assert "task_inbox.compact_storage" in calls
    assert "audit_store.maintain" in calls
    assert "snapshot" in calls
