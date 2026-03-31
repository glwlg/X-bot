"""Article publisher stages – shared data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageResult:
    """Uniform return value for every stage."""

    ok: bool
    data: dict[str, Any] | None = None
    output_path: str | None = None
    error: str | None = None
    failure_mode: str | None = None  # "recoverable" | "fatal"
    files: dict[str, bytes | str] = field(default_factory=dict)

    # ---------- helpers ----------
    @staticmethod
    def success(
        data: dict[str, Any] | None = None,
        *,
        output_path: str | None = None,
        files: dict[str, bytes | str] | None = None,
    ) -> "StageResult":
        return StageResult(
            ok=True,
            data=data,
            output_path=output_path,
            files=files or {},
        )

    @staticmethod
    def fail(
        error: str,
        *,
        failure_mode: str = "recoverable",
    ) -> "StageResult":
        return StageResult(
            ok=False,
            error=error,
            failure_mode=failure_mode,
        )
