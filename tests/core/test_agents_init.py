from __future__ import annotations

import importlib
import sys


def test_agents_package_init_does_not_import_skill_agent() -> None:
    sys.modules.pop("src.agents", None)
    sys.modules.pop("src.agents.skill_agent", None)

    module = importlib.import_module("src.agents")

    assert "src.agents.skill_agent" not in sys.modules
    assert module.__all__ == ()
