import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "extension"
        / "skills"
        / "builtin"
        / "skill_manager"
        / "scripts"
        / "execute.py"
    )
    module_name = f"skill_manager_execute_test_{id(path)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_ctx():
    return SimpleNamespace(message=SimpleNamespace(user=SimpleNamespace(id=123)))


@pytest.mark.asyncio
async def test_skill_manager_create_uses_coding_session(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    async def fake_create_with_coding_session(**kwargs):
        calls.append("coding_session")
        assert kwargs.get("backend") == "codex"
        return {
            "ok": True,
            "backend": "codex",
            "resolved_skill_name": "demo_skill",
            "skill_md": "---\nname: demo_skill\n---",
        }

    monkeypatch.setattr(
        module, "_create_with_coding_session", fake_create_with_coding_session
    )
    monkeypatch.setattr(module.skill_loader, "reload_skills", lambda: None)
    monkeypatch.setattr(
        module.skill_loader,
        "get_skill",
        lambda name: {"scripts": ["execute.py"]} if name == "demo_skill" else {},
    )

    result = await module.execute(
        _fake_ctx(),
        {
            "action": "create",
            "requirement": "create demo skill",
            "skill_name": "demo_skill",
        },
        runtime=object(),
    )

    assert calls == ["coding_session"]
    assert result["created_skill_name"] == "demo_skill"
    assert result["used_backend"] == "codex"
    assert result["has_scripts"] is True


@pytest.mark.asyncio
async def test_skill_manager_create_ignores_legacy_hint(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    async def fake_create_with_coding_session(**kwargs):
        calls.append("coding_session")
        assert kwargs.get("backend") == "gemini-cli"
        return {
            "ok": True,
            "backend": "gemini-cli",
            "resolved_skill_name": "demo_skill",
            "skill_md": "",
        }

    monkeypatch.setattr(
        module, "_create_with_coding_session", fake_create_with_coding_session
    )
    monkeypatch.setattr(module.skill_loader, "reload_skills", lambda: None)
    monkeypatch.setattr(module.skill_loader, "get_skill", lambda name: {"scripts": []})

    result = await module.execute(
        _fake_ctx(),
        {
            "action": "create",
            "requirement": "create demo skill",
            "skill_name": "demo_skill",
            "coding_backend": "gemini-cli",
            "coding_mode": "deprecated_mode",
        },
        runtime=object(),
    )

    assert calls == ["coding_session"]
    assert result["used_backend"] == "gemini-cli"


@pytest.mark.asyncio
async def test_skill_manager_modify_uses_coding_session(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    async def fake_modify_with_coding_session(**kwargs):
        calls.append("coding_session")
        assert kwargs.get("skill_name") == "demo_skill"
        return {"ok": True, "backend": "codex"}

    monkeypatch.setattr(
        module, "_modify_with_coding_session", fake_modify_with_coding_session
    )

    result = await module.execute(
        _fake_ctx(),
        {
            "action": "modify",
            "skill_name": "demo_skill",
            "instruction": "add one feature",
        },
        runtime=object(),
    )

    assert calls == ["coding_session"]
    assert "修改并生效" in str(result.get("text") or "")


@pytest.mark.asyncio
async def test_skill_manager_modify_ignores_legacy_hint(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    async def fake_modify_with_coding_session(**kwargs):
        calls.append("coding_session")
        assert kwargs.get("backend") == "codex"
        return {"ok": True, "backend": "codex"}

    monkeypatch.setattr(
        module, "_modify_with_coding_session", fake_modify_with_coding_session
    )

    result = await module.execute(
        _fake_ctx(),
        {
            "action": "modify",
            "skill_name": "demo_skill",
            "instruction": "fix bug",
            "coding_mode": "deprecated_mode",
        },
        runtime=object(),
    )

    assert calls == ["coding_session"]
    assert "修改并生效" in str(result.get("text") or "")


@pytest.mark.asyncio
async def test_skill_manager_create_accepts_opencode_backend(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    async def fake_create_with_coding_session(**kwargs):
        calls.append("coding_session")
        assert kwargs.get("backend") == "opencode"
        return {
            "ok": True,
            "backend": "opencode",
            "resolved_skill_name": "demo_skill",
            "skill_md": "",
        }

    monkeypatch.setattr(
        module, "_create_with_coding_session", fake_create_with_coding_session
    )
    monkeypatch.setattr(module.skill_loader, "reload_skills", lambda: None)
    monkeypatch.setattr(module.skill_loader, "get_skill", lambda name: {"scripts": []})

    result = await module.execute(
        _fake_ctx(),
        {
            "action": "create",
            "requirement": "create demo skill",
            "skill_name": "demo_skill",
            "coding_backend": "opencode",
        },
        runtime=object(),
    )

    assert calls == ["coding_session"]
    assert result["used_backend"] == "opencode"
