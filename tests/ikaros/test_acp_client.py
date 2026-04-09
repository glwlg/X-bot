from pathlib import Path

import pytest

from ikaros.dev.acp_client import (
    _resolve_workspace_path,
    _select_permission_outcome,
)


def test_select_permission_outcome_prefers_allow_once():
    outcome = _select_permission_outcome(
        [
            {"optionId": "reject", "kind": "reject_once", "name": "Reject"},
            {"optionId": "allow", "kind": "allow_once", "name": "Allow"},
        ]
    )

    assert outcome == {"outcome": "selected", "optionId": "allow"}


def test_resolve_workspace_path_rejects_escape(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    escaped = tmp_path / "outside.txt"
    escaped.write_text("hello\n", encoding="utf-8")

    with pytest.raises(ValueError):
        _resolve_workspace_path(
            workspace_root=workspace,
            raw_path=str(escaped.resolve()),
            allow_missing=False,
        )
