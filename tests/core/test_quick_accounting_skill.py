from types import SimpleNamespace

from sqlalchemy import MetaData

from api.core.database import Base
from core.skill_loader import skill_loader


def test_quick_accounting_skill_import_registers_users_table():
    module = skill_loader.import_skill_module("quick_accounting")

    assert module is not None
    assert "accounting_records" in Base.metadata.tables
    assert "users" in Base.metadata.tables


def test_quick_accounting_falls_back_to_stub_users_table(monkeypatch):
    module = skill_loader.import_skill_module("quick_accounting")

    assert module is not None

    fake_base = SimpleNamespace(metadata=MetaData())

    def fake_import_module(name: str):
        assert name == "api.auth.models"
        raise ModuleNotFoundError("No module named 'fastapi_users'", name="fastapi_users")

    monkeypatch.setattr(module, "Base", fake_base)
    monkeypatch.setattr(module.importlib, "import_module", fake_import_module)

    module._ensure_users_table_registered()

    assert "users" in fake_base.metadata.tables
