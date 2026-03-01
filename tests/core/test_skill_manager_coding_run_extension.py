import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.extension_executor as extension_executor_module
from core.tools.extension_tools import extension_tools


def _load_skill_manager_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "builtin"
        / "skill_manager"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location("skill_manager_execute_it", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill_manager_meta():
    return {
        "name": "skill_manager",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "skill_name": {"type": "string"},
                "requirement": {"type": "string"},
                "instruction": {"type": "string"},
            },
            "required": ["action"],
        },
        "entrypoint": "scripts/execute.py",
    }


def _ctx():
    return SimpleNamespace(message=SimpleNamespace(text="创建技能"))


@pytest.mark.asyncio
async def test_run_extension_skill_manager_create_routes_to_software_delivery(
    monkeypatch, tmp_path
):
    skill_manager_module = _load_skill_manager_module()
    skills_root = tmp_path / "skills_root"
    learned_skill_dir = skills_root / "learned" / "demo_skill"
    learned_skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = learned_skill_dir / "SKILL.md"
    skill_md_path.write_text("---\nname: demo_skill\n---\n", encoding="utf-8")

    state = {"index": {}, "reloads": 0}

    def fake_get_skill(name):
        if name == "skill_manager":
            return _skill_manager_meta()
        if name == "demo_skill":
            return {
                "name": "demo_skill",
                "source": "learned",
                "skill_dir": str(learned_skill_dir),
                "skill_md_path": str(skill_md_path),
                "scripts": ["execute.py"],
            }
        return None

    def fake_import_skill_module(skill_name, script_name="execute.py"):
        _ = script_name
        if skill_name == "skill_manager":
            return SimpleNamespace(execute=skill_manager_module.execute)
        return None

    def fake_get_skill_index():
        return dict(state["index"])

    def fake_reload_skills():
        state["reloads"] += 1
        state["index"] = {"demo_skill": {"source": "learned"}}

    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "skills_dir",
        str(skills_root),
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill",
        fake_get_skill,
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "import_skill_module",
        fake_import_skill_module,
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill_index",
        fake_get_skill_index,
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "reload_skills",
        fake_reload_skills,
    )

    calls: list[dict] = []

    class FakeManagerDevService:
        async def software_delivery(self, **kwargs):
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "summary": "template execution completed",
                "data": {
                    "backend": "codex",
                    "template_result": {
                        "ok": True,
                        "summary": "created",
                    },
                },
            }

    monkeypatch.setattr(
        skill_manager_module,
        "manager_dev_service",
        FakeManagerDevService(),
    )

    result = await extension_tools.run_extension(
        skill_name="skill_manager",
        args={
            "action": "create",
            "requirement": "create demo skill",
            "skill_name": "demo_skill",
        },
        ctx=_ctx(),
        runtime=object(),
    )

    assert calls
    assert calls[0].get("action") == "skill_create"
    assert calls[0].get("skill_name") == "demo_skill"
    assert "通过 `software_delivery`" in str(result.get("text") or "")


@pytest.mark.asyncio
async def test_run_extension_skill_manager_modify_failure_from_software_delivery(
    monkeypatch, tmp_path
):
    skill_manager_module = _load_skill_manager_module()
    skills_root = tmp_path / "skills_root"
    learned_skill_dir = skills_root / "learned" / "demo_skill"
    learned_skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = learned_skill_dir / "SKILL.md"
    skill_md_path.write_text("---\nname: demo_skill\n---\n", encoding="utf-8")

    def fake_get_skill(name):
        if name == "skill_manager":
            return _skill_manager_meta()
        if name == "demo_skill":
            return {
                "name": "demo_skill",
                "source": "learned",
                "skill_dir": str(learned_skill_dir),
                "skill_md_path": str(skill_md_path),
                "scripts": ["execute.py"],
            }
        return None

    def fake_import_skill_module(skill_name, script_name="execute.py"):
        _ = script_name
        if skill_name == "skill_manager":
            return SimpleNamespace(execute=skill_manager_module.execute)
        return None

    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "skills_dir",
        str(skills_root),
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill",
        fake_get_skill,
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "import_skill_module",
        fake_import_skill_module,
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "get_skill_index",
        lambda: {},
    )
    monkeypatch.setattr(
        extension_executor_module.skill_loader,
        "reload_skills",
        lambda: None,
    )

    calls: list[dict] = []

    class FakeManagerDevService:
        async def software_delivery(self, **kwargs):
            calls.append(dict(kwargs))
            return {
                "ok": False,
                "error_code": "skill_template_failed",
                "summary": "backend failed",
                "message": "backend failed",
                "data": {"backend": "codex"},
            }

    monkeypatch.setattr(
        skill_manager_module,
        "manager_dev_service",
        FakeManagerDevService(),
    )

    result = await extension_tools.run_extension(
        skill_name="skill_manager",
        args={
            "action": "modify",
            "skill_name": "demo_skill",
            "instruction": "fix skill bug",
        },
        ctx=_ctx(),
        runtime=object(),
    )

    assert calls
    assert calls[0].get("action") == "skill_modify"
    assert "模板任务失败" in str(result.get("text") or "")
