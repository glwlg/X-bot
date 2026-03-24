from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class ProgramManifest:
    program_id: str
    version: str
    entrypoint: str = "program.py"
    checksum: str = ""
    created_at: str = field(default_factory=now_iso)
    created_by: str = "manager"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "program_id": str(self.program_id or "").strip(),
            "version": str(self.version or "").strip(),
            "entrypoint": str(self.entrypoint or "program.py").strip(),
            "checksum": str(self.checksum or "").strip(),
            "created_at": str(self.created_at or now_iso()),
            "created_by": str(self.created_by or "manager"),
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ProgramManifest":
        data = dict(payload or {})
        raw_metadata = data.get("metadata")
        return cls(
            program_id=str(data.get("program_id") or "").strip(),
            version=str(data.get("version") or "").strip(),
            entrypoint=str(data.get("entrypoint") or "program.py").strip(),
            checksum=str(data.get("checksum") or "").strip(),
            created_at=str(data.get("created_at") or now_iso()),
            created_by=str(data.get("created_by") or "manager").strip(),
            metadata=dict(raw_metadata) if isinstance(raw_metadata, dict) else {},
        )


@dataclass
class SubagentProgramBinding:
    subagent_id: str
    program_id: str
    version: str
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subagent_id": str(self.subagent_id or "").strip(),
            "program_id": str(self.program_id or "").strip(),
            "version": str(self.version or "").strip(),
            "updated_at": str(self.updated_at or now_iso()),
        }
