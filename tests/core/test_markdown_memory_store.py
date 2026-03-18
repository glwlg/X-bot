import json

from core.audit_store import audit_store
from core.markdown_memory_store import markdown_memory_store


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
    audit_store._legacy_migrated = False


def test_markdown_memory_store_migrates_legacy_memory_json(tmp_path, monkeypatch):
    _redirect_audit_paths(tmp_path)
    monkeypatch.setenv("DATA_DIR", str((tmp_path / "data").resolve()))
    user_root = (tmp_path / "data" / "user").resolve()
    user_root.mkdir(parents=True, exist_ok=True)

    legacy_lines = [
        {
            "type": "entity",
            "name": "User",
            "entityType": "Person",
            "observations": ["当前交互用户", "偏好称呼：主人"],
        },
        {
            "type": "entity",
            "name": "江苏无锡",
            "entityType": "location",
            "observations": ["由用户提供的地点信息：江苏无锡"],
        },
        {
            "type": "relation",
            "from": "User",
            "to": "江苏无锡",
            "relationType": "lives in",
        },
    ]
    (user_root / "memory.json").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in legacy_lines) + "\n",
        encoding="utf-8",
    )

    markdown_memory_store.ensure_migrated("u1")

    memory_text = (user_root / "MEMORY.md").read_text(encoding="utf-8")
    assert "偏好称呼：主人" in memory_text
    assert "居住地：江苏无锡" in memory_text


def test_markdown_memory_store_remember_deduplicates(tmp_path, monkeypatch):
    _redirect_audit_paths(tmp_path)
    monkeypatch.setenv("DATA_DIR", str((tmp_path / "data").resolve()))

    ok_1, _ = markdown_memory_store.remember("u2", "请记住我住在北京", source="test")
    ok_2, _ = markdown_memory_store.remember("u2", "请记住我住在北京", source="test")
    assert ok_1 is True
    assert ok_2 is True

    memory_path = markdown_memory_store.memory_path("u2")
    memory_text = memory_path.read_text(encoding="utf-8")
    assert memory_text.count("居住地：北京") == 1

    daily_path = markdown_memory_store.daily_path("u2")
    daily_text = daily_path.read_text(encoding="utf-8")
    assert "请记住我住在北京" in daily_text
