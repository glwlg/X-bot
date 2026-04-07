import importlib.util
from pathlib import Path

import pytest


def _load_store_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "extension"
        / "skills"
        / "builtin"
        / "credential_manager"
        / "scripts"
        / "store.py"
    )
    spec = importlib.util.spec_from_file_location(
        "credential_manager_store_test",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _MemoryStorage:
    def __init__(self, initial=None):
        self.payload = initial if initial is not None else {}

    async def read(self, _path, default):
        if self.payload is None:
            return default
        return self.payload

    async def write(self, _path, payload):
        self.payload = payload


@pytest.mark.asyncio
async def test_credential_store_reads_legacy_single_entry_payload(monkeypatch):
    module = _load_store_module()
    storage = _MemoryStorage(
        {
            "wechat_official_account": {
                "data": {"app_id": "wx-1", "app_secret": "secret-1"},
                "updated_at": "2026-04-07T08:00:00Z",
            }
        }
    )
    monkeypatch.setattr(module, "storage_service", storage)

    credential = await module.get_credential("u-1", "wechat_official_account")
    entries = await module.list_credential_entries("u-1", "wechat_official_account")

    assert credential == {"app_id": "wx-1", "app_secret": "secret-1"}
    assert len(entries) == 1
    assert entries[0]["id"] == "default"
    assert entries[0]["name"] == "wechat_official_account"
    assert entries[0]["is_default"] is True


@pytest.mark.asyncio
async def test_credential_store_supports_multiple_entries_and_default_selection(monkeypatch):
    module = _load_store_module()
    storage = _MemoryStorage()
    monkeypatch.setattr(module, "storage_service", storage)

    first = await module.upsert_credential_entry(
        "u-1",
        "wechat_official_account",
        name="主号",
        data={"app_id": "wx-main", "app_secret": "main-secret"},
        set_default=True,
    )
    second = await module.upsert_credential_entry(
        "u-1",
        "wechat_official_account",
        name="副号",
        data={"app_id": "wx-side", "app_secret": "side-secret"},
    )

    assert first is not None
    assert second is not None

    default_credential = await module.get_credential("u-1", "wechat_official_account")
    selected_by_name = await module.get_credential("u-1", "wechat_official_account", "副号")
    selected_by_id = await module.get_credential("u-1", "wechat_official_account", second["id"])

    assert default_credential == {"app_id": "wx-main", "app_secret": "main-secret"}
    assert selected_by_name == {"app_id": "wx-side", "app_secret": "side-secret"}
    assert selected_by_id == {"app_id": "wx-side", "app_secret": "side-secret"}

    await module.set_default_credential_entry("u-1", "wechat_official_account", second["id"])
    next_default = await module.get_credential("u-1", "wechat_official_account")

    assert next_default == {"app_id": "wx-side", "app_secret": "side-secret"}
