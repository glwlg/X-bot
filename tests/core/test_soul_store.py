from core.audit_store import audit_store
from core.soul_store import SoulStore


def _redirect_audit_paths(tmp_path):
    audit_root = (tmp_path / "audit").resolve()
    versions_root = (tmp_path / "versions").resolve()
    audit_root.mkdir(parents=True, exist_ok=True)
    versions_root.mkdir(parents=True, exist_ok=True)
    audit_store.audit_root = audit_root
    audit_store.versions_root = versions_root
    audit_store.events_path = (audit_root / "events.jsonl").resolve()


def test_soul_store_load_update_and_rollback(tmp_path):
    _redirect_audit_paths(tmp_path)
    store = SoulStore()
    store.kernel_root = (tmp_path / "kernel" / "core-manager").resolve()
    store.userland_root = (tmp_path / "userland" / "workers").resolve()
    store.kernel_root.mkdir(parents=True, exist_ok=True)
    store.userland_root.mkdir(parents=True, exist_ok=True)

    core = store.load_core()
    assert "Core Manager SOUL" in core.content

    update = store.update_core(
        "# Core Manager SOUL\n- test: true\n",
        actor="tester",
        reason="unit_test_update",
    )
    assert update["path"]
    versions = store.list_versions(agent_kind="core-manager", limit=5)
    assert versions
    version_id = str(versions[0].get("version_id", ""))
    assert version_id
    assert store.rollback_core(version_id, actor="tester") is True

    worker = store.load_worker("worker-main")
    assert "Worker SOUL" in worker.content
