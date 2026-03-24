from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from core.file_artifacts import normalize_file_rows


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class SubagentResult:
    subagent_id: str
    ok: bool
    summary: str = ""
    text: str = ""
    error: str = ""
    files: List[Dict[str, str]] = field(default_factory=list)
    diagnostic_summary: str = ""
    task_outcome: str = ""
    failure_mode: str = ""
    manager_followup_required: bool = False
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subagent_id": str(self.subagent_id or "").strip(),
            "ok": bool(self.ok),
            "summary": str(self.summary or "").strip(),
            "text": str(self.text or "").strip(),
            "error": str(self.error or "").strip(),
            "files": normalize_file_rows(self.files),
            "diagnostic_summary": str(self.diagnostic_summary or "").strip(),
            "task_outcome": str(self.task_outcome or "").strip().lower(),
            "failure_mode": str(self.failure_mode or "").strip().lower(),
            "manager_followup_required": bool(self.manager_followup_required),
            "created_at": str(self.created_at or _now_iso()),
        }
