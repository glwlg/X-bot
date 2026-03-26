from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.config import DATA_DIR
from shared.queue.jsonl_queue import FileLock


AUDIT_PATH = (Path(DATA_DIR) / "kernel" / "admin-audit.jsonl").resolve()
AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


async def record_admin_audit(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "ts": _now_iso(),
        **dict(payload or {}),
    }
    lock = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".lock")
    async with FileLock(lock):
        with AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    return normalized


async def list_admin_audits(*, limit: int = 100) -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    lock = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".lock")
    async with FileLock(lock):
        rows: list[dict[str, Any]] = []
        for raw_line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
            text = str(raw_line or "").strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    rows.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
    return rows[: max(1, int(limit))]
