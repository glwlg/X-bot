import importlib.util
from pathlib import Path

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "extension"
        / "skills"
        / "learned"
        / "deep_research"
        / "scripts"
        / "execute.py"
    )
    spec = importlib.util.spec_from_file_location("deep_research_execute_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deep_research_resolves_web_search_execute_from_extension_layout():
    module = _load_module()
    expected = (
        Path(__file__).resolve().parents[2]
        / "extension"
        / "skills"
        / "builtin"
        / "web_search"
        / "scripts"
        / "execute.py"
    )

    assert module._resolve_web_search_execute_path() == expected


def test_deep_research_resolve_web_search_execute_falls_back_to_legacy_layout(
    tmp_path,
):
    module = _load_module()
    legacy = tmp_path / "skills" / "builtin" / "web_search" / "scripts" / "execute.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("VALUE = 1\n", encoding="utf-8")

    assert module._resolve_web_search_execute_path(tmp_path) == legacy


def test_deep_research_resolve_web_search_execute_raises_when_missing(tmp_path):
    module = _load_module()

    with pytest.raises(RuntimeError, match="web_search execute module unavailable"):
        module._resolve_web_search_execute_path(tmp_path)
