from __future__ import annotations

import builtins
import json
import sys
import types
from datetime import date

import pytest

import core.long_term_memory as long_term_memory_module
import core.memory_config as memory_config_module
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
    audit_store._legacy_migrated = False


def _reset_long_term_memory_state():
    memory_config_module.reset_memory_config_cache()
    long_term_memory_module.long_term_memory._provider = None
    long_term_memory_module.long_term_memory._provider_name = ""
    long_term_memory_module.long_term_memory._initialized = False
    long_term_memory_module.long_term_memory._init_lock = None
    long_term_memory_module.long_term_memory._manager_snapshot_cache = ""


def _write_memory_config(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_long_term_memory_file_provider_keeps_generic_snapshot(
    tmp_path, monkeypatch
):
    _redirect_audit_paths(tmp_path)
    data_dir = (tmp_path / "data").resolve()
    config_path = (tmp_path / "memory.json").resolve()
    _write_memory_config(
        config_path,
        {"provider": "file", "providers": {"file": {}, "mem0": {}}},
    )
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("MEMORY_CONFIG_PATH", str(config_path))
    _reset_long_term_memory_state()

    ok, detail = await long_term_memory_module.long_term_memory.remember_user(
        "u-file",
        "请记住我住在北京",
        source="test",
    )
    snapshot = await long_term_memory_module.long_term_memory.load_user_snapshot(
        "u-file",
        include_daily=True,
        max_chars=2000,
    )

    assert ok is True
    assert "居住地：北京" in detail
    assert "【长期记忆】" in snapshot
    assert "MEMORY.md" not in snapshot
    assert "【近期记忆" in snapshot
    assert "居住地：北京" in (
        data_dir / "user" / "MEMORY.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_long_term_memory_mem0_provider_reads_and_writes_without_file_store(
    tmp_path, monkeypatch
):
    _redirect_audit_paths(tmp_path)
    data_dir = (tmp_path / "data").resolve()
    config_path = (tmp_path / "memory.json").resolve()
    _write_memory_config(
        config_path,
        {
            "provider": "mem0",
            "providers": {"file": {}, "mem0": {"kwargs": {"project": "demo"}}},
        },
    )
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("MEMORY_CONFIG_PATH", str(config_path))
    _reset_long_term_memory_state()

    class _FakeAsyncMemory:
        instances: list["_FakeAsyncMemory"] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.user_rows: dict[str, list[str]] = {}
            self.agent_rows: dict[str, list[str]] = {}
            self.__class__.instances.append(self)

        async def get_all(self, *, user_id=None, agent_id=None):
            if user_id is not None:
                return {
                    "results": [
                        {"memory": item, "created_at": f"u-{index}"}
                        for index, item in enumerate(self.user_rows.get(str(user_id), []))
                    ]
                }
            return {
                "results": [
                    {"memory": item, "created_at": f"m-{index}"}
                    for index, item in enumerate(self.agent_rows.get(str(agent_id), []))
                ]
            }

        async def add(self, *, messages, user_id=None, agent_id=None):
            content = str(messages[0]["content"] or "").strip()
            if user_id is not None:
                self.user_rows.setdefault(str(user_id), []).append(content)
            else:
                self.agent_rows.setdefault(str(agent_id), []).append(content)
            return {"ok": True}

    monkeypatch.setitem(sys.modules, "mem0", types.SimpleNamespace(AsyncMemory=_FakeAsyncMemory))

    await long_term_memory_module.long_term_memory.initialize()
    ok, detail = await long_term_memory_module.long_term_memory.remember_user(
        "u-mem0",
        "以后请叫我老王",
        source="test",
    )
    user_snapshot = await long_term_memory_module.long_term_memory.load_user_snapshot(
        "u-mem0",
        include_daily=True,
        max_chars=2000,
    )
    added = await long_term_memory_module.long_term_memory.add_manager_experiences(
        ["优先验证配置"],
        day=date(2026, 3, 19),
        source_user_id="u-mem0",
    )
    manager_snapshot = long_term_memory_module.long_term_memory.load_manager_snapshot(
        max_chars=2000
    )

    assert ok is True
    assert "偏好称呼：老王" in detail
    assert "【长期记忆】" in user_snapshot
    assert "偏好称呼：老王" in user_snapshot
    assert added == 1
    assert "- [2026-03-19] 优先验证配置" in manager_snapshot
    assert _FakeAsyncMemory.instances[0].kwargs == {"project": "demo"}
    assert not (data_dir / "user" / "MEMORY.md").exists()
    assert not (data_dir / "system" / "MANAGER_MEMORY.md").exists()
    assert (data_dir / "user" / "memory").exists()
    assert (data_dir / "system" / "manager_memory" / "2026-03-19.md").exists()


@pytest.mark.asyncio
async def test_long_term_memory_mem0_provider_fails_when_package_missing(
    tmp_path, monkeypatch
):
    _redirect_audit_paths(tmp_path)
    data_dir = (tmp_path / "data").resolve()
    config_path = (tmp_path / "memory.json").resolve()
    _write_memory_config(
        config_path,
        {"provider": "mem0", "providers": {"file": {}, "mem0": {}}},
    )
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("MEMORY_CONFIG_PATH", str(config_path))
    _reset_long_term_memory_state()
    monkeypatch.delitem(sys.modules, "mem0", raising=False)

    original_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mem0" or name.startswith("mem0."):
            raise ImportError("missing mem0")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(RuntimeError, match="mem0ai is not installed"):
        await long_term_memory_module.long_term_memory.initialize()
