from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from core.config import DATA_DIR
from core.heartbeat_store import HeartbeatStore
from core import state_io, state_store
from core.state_io import now_iso, read_json
from core.state_paths import system_path

MIGRATION_REPORT_PATH_PARTS = ("state_migrations", "legacy_user_state.md")
DOMAIN_ORDER = ["heartbeat", "reminders", "rss", "scheduler", "watchlist"]


def _empty_counts() -> dict[str, int]:
    return {"migrated": 0, "skipped": 0, "ambiguous": 0}


async def _read_current_reminder_rows(user_id: str) -> list[dict[str, Any]]:
    rows = state_store._read_row_list(
        await read_json(state_store._reminders_path(user_id), []), "reminders"
    )
    return [state_store._normalize_reminder(item, user_id=user_id) for item in rows]


async def _read_current_scheduled_task_rows(user_id: str) -> list[dict[str, Any]]:
    rows = state_store._read_row_list(
        await read_json(state_store._scheduled_tasks_path(user_id), []),
        "scheduled_tasks",
        "tasks",
    )
    return [
        state_store._normalize_scheduled_task(item, user_id=user_id) for item in rows
    ]


async def _read_current_watchlist_rows(user_id: str) -> list[dict[str, Any]]:
    rows = state_store._read_row_list(
        await read_json(state_store._watchlist_path(user_id), [])
    )
    normalized_rows = [state_store._normalize_watchlist_row(item) for item in rows]
    return [item for item in normalized_rows if item.get("stock_code")]


def _normalize_migrated_reminder(
    row: dict[str, Any], user_id: str
) -> dict[str, Any] | None:
    normalized = state_store._normalize_reminder(row, user_id=user_id)
    return normalized if int(normalized.get("id") or 0) > 0 else None


def _normalize_migrated_scheduled_task(
    row: dict[str, Any], user_id: str
) -> dict[str, Any] | None:
    normalized = state_store._normalize_scheduled_task(row, user_id=user_id)
    return normalized if int(normalized.get("id") or 0) > 0 else None


async def _migrate_rows_by_user(
    *,
    legacy_path,
    list_keys: tuple[str, ...],
    normalize_row: Callable[[dict[str, Any], str], dict[str, Any] | None],
    read_current_rows: Callable[[str], Any],
    write_rows: Callable[[str, list[dict[str, Any]]], Any],
    key_for_row: Callable[[dict[str, Any]], Any],
) -> dict[str, int]:
    counts = _empty_counts()
    legacy_rows = state_store._read_row_list(
        await read_json(legacy_path, []), *list_keys
    )
    rows_by_user: dict[str, list[dict[str, Any]]] = {}

    for raw_row in legacy_rows:
        user_id = state_store._row_user_id(raw_row)
        if not user_id:
            counts["ambiguous"] += 1
            continue
        try:
            normalized_row = normalize_row(raw_row, user_id)
        except Exception:
            counts["ambiguous"] += 1
            continue
        if normalized_row is None:
            counts["ambiguous"] += 1
            continue
        rows_by_user.setdefault(user_id, []).append(normalized_row)

    for user_id, legacy_user_rows in rows_by_user.items():
        current_rows = list(await read_current_rows(user_id))
        seen = set()
        for current_row in current_rows:
            try:
                seen.add(key_for_row(current_row))
            except Exception:
                continue
        updated_rows = list(current_rows)
        for legacy_row in legacy_user_rows:
            try:
                row_key = key_for_row(legacy_row)
            except Exception:
                counts["ambiguous"] += 1
                continue
            if row_key in seen:
                counts["skipped"] += 1
                continue
            seen.add(row_key)
            updated_rows.append(legacy_row)
            counts["migrated"] += 1
        if len(updated_rows) != len(current_rows):
            await write_rows(user_id, updated_rows)

    return counts


async def _migrate_scheduler() -> dict[str, int]:
    return await _migrate_rows_by_user(
        legacy_path=state_store._legacy_scheduled_tasks_path(),
        list_keys=("scheduled_tasks", "tasks"),
        normalize_row=_normalize_migrated_scheduled_task,
        read_current_rows=_read_current_scheduled_task_rows,
        write_rows=state_store._write_user_scheduled_tasks,
        key_for_row=lambda row: int(row.get("id") or 0),
    )


async def _migrate_rss() -> dict[str, int]:
    return await _migrate_rows_by_user(
        legacy_path=state_store._legacy_subs_path(),
        list_keys=(),
        normalize_row=lambda row, _user_id: state_store._normalize_subscription(row),
        read_current_rows=state_store._read_current_subscription_rows,
        write_rows=state_store._write_subscription_rows,
        key_for_row=lambda row: int(row.get("id") or 0),
    )


async def _migrate_reminders() -> dict[str, int]:
    return await _migrate_rows_by_user(
        legacy_path=state_store._legacy_reminders_path(),
        list_keys=("reminders",),
        normalize_row=_normalize_migrated_reminder,
        read_current_rows=_read_current_reminder_rows,
        write_rows=state_store._write_user_reminders,
        key_for_row=lambda row: int(row.get("id") or 0),
    )


async def _migrate_watchlist() -> dict[str, int]:
    return await _migrate_rows_by_user(
        legacy_path=state_store._legacy_watchlist_path(),
        list_keys=(),
        normalize_row=lambda row, _user_id: (
            normalized
            if (normalized := state_store._normalize_watchlist_row(row)).get(
                "stock_code"
            )
            else None
        ),
        read_current_rows=_read_current_watchlist_rows,
        write_rows=state_store._write_watchlist,
        key_for_row=lambda row: (
            str(row.get("stock_code") or "").strip().lower(),
            str(row.get("platform") or "telegram").strip().lower(),
        ),
    )


async def _migrate_heartbeat() -> dict[str, int]:
    counts = _empty_counts()
    store = HeartbeatStore()
    store.root = (
        Path(os.getenv("DATA_DIR", str(DATA_DIR))).resolve() / "runtime_tasks"
    ).resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    shared_heartbeat_exists = store._legacy_shared_heartbeat_path().exists()
    shared_status_payload = store._read_legacy_shared_status_payload()

    parsed: dict[str, Any] = {}
    if shared_heartbeat_exists:
        raw_text = store._legacy_shared_heartbeat_path().read_text(encoding="utf-8")
        parsed, _checklist = store._parse_markdown(raw_text)

    owners = sorted(store._legacy_shared_owner_ids(parsed, shared_status_payload))
    if (shared_heartbeat_exists or shared_status_payload is not None) and not owners:
        counts["ambiguous"] += 1
        return counts

    for user_id in owners:
        legacy_state = store._legacy_shared_state(user_id)
        if legacy_state is None:
            counts["ambiguous"] += 1
            continue
        spec, checklist, status = legacy_state
        heartbeat_exists = store.heartbeat_path(user_id).exists()
        status_exists = store.status_path(user_id).exists()
        if heartbeat_exists and status_exists:
            counts["skipped"] += 1
            continue
        if not heartbeat_exists:
            store.heartbeat_path(user_id).write_text(
                store._render_markdown(spec, checklist), encoding="utf-8"
            )
        if not status_exists:
            store._write_status_unlocked(user_id, status)
        counts["migrated"] += 1

    return counts


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
    domain_counts = {
        "heartbeat": await _migrate_heartbeat(),
        "reminders": await _migrate_reminders(),
        "rss": await _migrate_rss(),
        "scheduler": await _migrate_scheduler(),
        "watchlist": await _migrate_watchlist(),
    }
    summary = _empty_counts()
    for counts in domain_counts.values():
        for key in summary:
            summary[key] += int(counts.get(key) or 0)

    report = {
        "report_name": "legacy_user_state",
        "run_at": now_iso(),
        "summary": {**summary, "domains": list(DOMAIN_ORDER)},
        "domains": domain_counts,
    }
    await _persist_report(report)
    return report
