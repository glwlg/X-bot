from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from manager.dev.runtime import run_shell


class ManagerDevValidator:
    def detect_commands(
        self,
        *,
        repo_path: str,
        validation_commands: List[str] | None,
    ) -> List[str]:
        explicit = [
            str(item).strip()
            for item in list(validation_commands or [])
            if str(item).strip()
        ]
        if explicit:
            return explicit

        root = Path(str(repo_path or "").strip() or ".").resolve()
        if (root / "pyproject.toml").exists():
            return ["uv run pytest"]
        if (root / "package.json").exists():
            return ["npm test"]
        if (root / "Cargo.toml").exists():
            return ["cargo test"]
        return []

    async def validate(
        self,
        *,
        repo_path: str,
        validation_commands: List[str] | None = None,
        timeout_sec: int = 1800,
    ) -> Dict[str, Any]:
        commands = self.detect_commands(
            repo_path=repo_path,
            validation_commands=validation_commands,
        )
        if not commands:
            return {
                "ok": True,
                "commands": [],
                "summary": "No validation command detected",
            }

        safe_timeout = max(
            60,
            int(
                timeout_sec
                or int(os.getenv("DEV_VALIDATION_TIMEOUT_SEC", "1800") or "1800")
            ),
        )

        rows: List[Dict[str, Any]] = []
        for command in commands:
            result = await run_shell(
                command,
                cwd=repo_path,
                timeout_sec=safe_timeout,
            )
            row = {
                "command": str(command),
                "ok": bool(result.get("ok")),
                "exit_code": int(result.get("exit_code") or 0),
                "summary": str(result.get("summary") or "").strip(),
                "stdout": str(result.get("stdout") or ""),
                "stderr": str(result.get("stderr") or ""),
            }
            rows.append(row)
            if not row["ok"]:
                return {
                    "ok": False,
                    "commands": rows,
                    "summary": f"Validation failed: {row['command']}",
                }

        return {
            "ok": True,
            "commands": rows,
            "summary": f"Validation passed: {len(rows)} command(s)",
        }


manager_dev_validator = ManagerDevValidator()
