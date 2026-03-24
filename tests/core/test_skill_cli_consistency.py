from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOTS = [
    REPO_ROOT / "skills" / "builtin",
    REPO_ROOT / "skills" / "learned",
]


def _iter_active_skill_dirs() -> list[Path]:
    skill_dirs: list[Path] = []
    for root in SKILL_ROOTS:
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                skill_dirs.append(child)
    return skill_dirs


def _load_frontmatter(skill_md_path: Path) -> dict:
    content = skill_md_path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    assert len(parts) >= 3, f"{skill_md_path} must contain YAML frontmatter"
    data = yaml.safe_load(parts[1]) or {}
    assert isinstance(data, dict), f"{skill_md_path} frontmatter must be a mapping"
    return data


def test_entrypoint_skills_expose_cli_contract() -> None:
    for skill_dir in _iter_active_skill_dirs():
        skill_md_path = skill_dir / "SKILL.md"
        content = skill_md_path.read_text(encoding="utf-8")
        frontmatter = _load_frontmatter(skill_md_path)
        entrypoint = str(frontmatter.get("entrypoint") or "").strip()
        if entrypoint != "scripts/execute.py":
            continue

        script_path = skill_dir / "scripts" / "execute.py"
        assert script_path.exists(), f"{script_path} must exist"

        permissions = frontmatter.get("permissions") or {}
        assert isinstance(permissions.get("shell"), bool), (
            f"{skill_md_path} must declare permissions.shell as a boolean"
        )

        script_text = script_path.read_text(encoding="utf-8")
        assert 'if __name__ == "__main__":' in script_text, (
            f"{script_path} must provide a CLI entrypoint"
        )
        assert "python scripts/execute.py" in content, (
            f"{skill_md_path} must document how to call the CLI"
        )
