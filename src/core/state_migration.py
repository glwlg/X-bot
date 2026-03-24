from __future__ import annotations

from typing import Any

import os
from pathlib import Path

from core.config import DATA_DIR
from core.heartbeat_store import HeartbeatStore
from core import state_io
from core.state_io import now_iso
from core.state_paths import system_path

MIGRATION_REPORT_PATH_PARTS = ("state_migrations", "legacy_user_state.md")
DOMAIN_ORDER = ["heartbeat", "reminders", "rss", "scheduler", "watchlist"]


def _empty_counts() -> dict[str, int]:
    return {"migrated": 0, "skipped": 0, "ambiguous": 0}


async def _persist_report(report: dict[str, Any]) -> None:
    path = system_path(*MIGRATION_REPORT_PATH_PARTS)
    lock = state_io._lock_for(path)
    async with lock:
        existing = state_io._read_json_sync(path, {})
        history = existing.get("history") if isinstance(existing, dict) else None
        if not isinstance(history, list):
            history = []

        payload = dict(report)
        payload["history"] = [
            *history[-19:],
            {
                "run_at": report["run_at"],
                "summary": report["summary"],
                "domains": report["domains"],
            },
        ]
        state_io._write_json_sync(path, payload)


async def migrate_legacy_user_state() -> dict[str, Any]:
    store = HeartbeatStore()
    store.root = (Path(os.getenv("DATA_DIR", str(DATA_DIR))).resolve() / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    cleaned = await store.normalize_runtime_tree()
    domain_counts = {name: _empty_counts() for name in DOMAIN_ORDER}
    domain_counts["heartbeat"]["skipped"] = int(cleaned)
    summary = _empty_counts()
    summary["skipped"] = int(cleaned)

    report = {
        "report_name": "legacy_user_state",
        "run_at": now_iso(),
        "summary": {**summary, "domains": list(DOMAIN_ORDER)},
        "domains": domain_counts,
    }
    await _persist_report(report)
    return report
