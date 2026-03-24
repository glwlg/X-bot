from __future__ import annotations

import tomllib
from pathlib import Path


def test_api_dependency_group_includes_bot_runtime():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    groups = data.get("dependency-groups", {})
    api_group = groups.get("api", [])

    assert {"include-group": "bot-runtime"} in api_group
