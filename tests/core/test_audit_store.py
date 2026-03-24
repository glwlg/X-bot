import json
from pathlib import Path

from core.audit_store import audit_store


def _redirect_audit_paths(tmp_path):
    audit_root = (tmp_path / "audit").resolve()
    versions_root = (tmp_path / "versions").resolve()
    index_root = (audit_root / "index").resolve()
    logs_root = (audit_root / "logs").resolve()
    audit_root.mkdir(parents=True, exist_ok=True)
    versions_root.mkdir(parents=True, exist_ok=True)
    index_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)
    audit_store.audit_root = audit_root
    audit_store.versions_root = versions_root
    audit_store.index_root = index_root
    audit_store.logs_root = logs_root
    audit_store.events_path = (audit_root / "events.jsonl").resolve()
    audit_store.version_retention_count = 3
    audit_store.log_retention_days = 30
    audit_store._legacy_migrated = False


def test_audit_store_prunes_old_versions(tmp_path):
    _redirect_audit_paths(tmp_path)
    target = (tmp_path / "state.txt").resolve()
    target.write_text("v0", encoding="utf-8")

    previous_ids = []
    for index in range(1, 6):
        result = audit_store.write_versioned(
            target,
            f"v{index}",
            actor="tester",
            reason=f"write-{index}",
        )
        previous_ids.append(str(result.get("previous_version_id") or ""))

    versions = audit_store.list_versions(target, limit=10)
    history_files = sorted(audit_store._history_dir(target).glob("*.bak"))

    assert len(versions) == 3
    assert len(history_files) == 3
    assert previous_ids[0] not in {row["version_id"] for row in versions}
    assert audit_store.rollback(target, previous_ids[0], actor="tester") is False


def test_audit_store_migrates_legacy_events_jsonl_to_index(tmp_path):
    _redirect_audit_paths(tmp_path)
    target = (tmp_path / "legacy.txt").resolve()
    target.write_text("legacy", encoding="utf-8")
    history_dir = audit_store._history_dir(target)
    snapshot_path = (history_dir / "20260318000000-legacy.bak").resolve()
    snapshot_path.write_text("old", encoding="utf-8")
    payload = {
        "ts": "2026-03-18T00:00:00+08:00",
        "event": "snapshot",
        "category": "generic",
        "actor": "tester",
        "reason": "legacy",
        "target": str(target),
        "version_id": "20260318000000-legacy",
        "snapshot_path": str(snapshot_path),
    }
    audit_store.events_path.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    audit_store.maintain()

    versions = audit_store.list_versions(target, limit=5)
    legacy_logs = list(audit_store.logs_root.glob("legacy-*.jsonl"))

    assert len(versions) == 1
    assert versions[0]["version_id"] == "20260318000000-legacy"
    assert legacy_logs
    assert audit_store.events_path.exists() is False
