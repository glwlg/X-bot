from __future__ import annotations

import ast
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
WORKER_SAFE_IMPORT_FILES = [
    REPO_ROOT / "src" / "user_context.py",
    REPO_ROOT
    / "skills"
    / "builtin"
    / "download_video"
    / "scripts"
    / "execute.py",
    REPO_ROOT
    / "skills"
    / "builtin"
    / "download_video"
    / "scripts"
    / "services"
    / "download_service.py",
]


def _top_level_import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                targets.add(node.module)
    return targets


def _flatten_dependency_group(
    groups: dict[str, list[object]],
    group_name: str,
    *,
    seen: set[str] | None = None,
) -> list[str]:
    if seen is None:
        seen = set()
    if group_name in seen:
        return []
    seen.add(group_name)

    flattened: list[str] = []
    for item in groups.get(group_name, []):
        if isinstance(item, str):
            flattened.append(item)
            continue
        if isinstance(item, dict):
            nested = item.get("include-group")
            if isinstance(nested, str):
                flattened.extend(
                    _flatten_dependency_group(groups, nested, seen=seen.copy())
                )
    return flattened


def test_worker_shared_modules_avoid_top_level_telegram_imports() -> None:
    for path in WORKER_SAFE_IMPORT_FILES:
        imports = _top_level_import_targets(path)
        assert "telegram" not in imports
        assert "telegram.ext" not in imports
        assert "telegram.error" not in imports


def test_worker_dependency_groups_exclude_platform_sdks() -> None:
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    groups = payload["dependency-groups"]

    worker_runtime = _flatten_dependency_group(groups, "worker-runtime")
    worker = _flatten_dependency_group(groups, "worker")
    manager_runtime = _flatten_dependency_group(groups, "manager-runtime")

    assert not any(dep.startswith("python-telegram-bot") for dep in worker_runtime)
    assert not any(dep.startswith("discord-py") for dep in worker_runtime)
    assert not any(dep.startswith("dingtalk-stream") for dep in worker_runtime)
    assert not any(dep.startswith("python-telegram-bot") for dep in worker)
    assert any(dep.startswith("python-telegram-bot") for dep in manager_runtime)
