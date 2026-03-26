from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping
from uuid import uuid4

from ikaros.dev.session_paths import (
    coding_session_events_path,
    coding_session_path,
    ensure_coding_session_root,
)


_LOCKS: dict[str, asyncio.Lock] = {}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _lock_for(session_id: str) -> asyncio.Lock:
    key = _clean_text(session_id) or "session"
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _read_json_dict(path: Path) -> dict[str, object] | None:
    try:
        if not path.exists():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return dict(loaded) if isinstance(loaded, dict) else None


def _read_events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            loaded = json.loads(line)
        except Exception:
            continue
        if isinstance(loaded, dict):
            rows.append(dict(loaded))
    return rows


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")


def _dedupe_key(event: Mapping[str, object]) -> tuple[str, str] | None:
    source = _clean_text(event.get("source"))
    source_event_id = _clean_text(event.get("source_event_id"))
    if not source or not source_event_id:
        return None
    return source, source_event_id


def _find_duplicate(
    events: Iterable[Mapping[str, object]], event: Mapping[str, object]
) -> dict[str, object] | None:
    key = _dedupe_key(event)
    if key is None:
        return None
    for candidate in events:
        if _dedupe_key(candidate) == key:
            return dict(candidate)
    return None


def _normalize_event(
    *, session_id: str, event: Mapping[str, object]
) -> dict[str, object]:
    normalized: dict[str, object] = {
        "event_id": _clean_text(event.get("event_id")) or f"evt-{uuid4().hex}",
        "session_id": _clean_text(session_id),
        "kind": _clean_text(event.get("kind")) or "event",
        "source": _clean_text(event.get("source")) or "ikaros",
        "source_event_id": _clean_text(event.get("source_event_id")),
        "turn_id": _clean_text(event.get("turn_id")),
        "runtime_binding_id": _clean_text(event.get("runtime_binding_id")),
        "created_at": _clean_text(event.get("created_at")) or _now_iso(),
    }
    for key, value in dict(event).items():
        token = str(key or "").strip()
        if token and token not in normalized:
            normalized[token] = value
    return normalized


def _base_session_projection(session_id: str) -> dict[str, object]:
    return {
        "session_id": _clean_text(session_id),
        "workspace_id": "",
        "repo_root": "",
        "backend": "",
        "transport": "",
        "status": "created",
        "current_turn_id": "",
        "runtime_binding_id": "",
        "created_at": "",
        "updated_at": "",
    }


def _apply_session_event(
    session: Mapping[str, object], event: Mapping[str, object]
) -> dict[str, object]:
    updated = dict(session)
    kind = _clean_text(event.get("kind"))
    created_at = _clean_text(event.get("created_at"))
    runtime_binding_id = _clean_text(event.get("runtime_binding_id"))

    if kind == "session_created":
        updated["session_id"] = _clean_text(event.get("session_id")) or _clean_text(
            updated.get("session_id")
        )
        updated["workspace_id"] = _clean_text(event.get("workspace_id"))
        updated["repo_root"] = _clean_text(event.get("repo_root"))
        updated["backend"] = _clean_text(event.get("backend"))
        updated["transport"] = _clean_text(event.get("transport"))
        updated["status"] = _clean_text(event.get("status")) or "running"
        updated["created_at"] = created_at or _clean_text(updated.get("created_at"))

    if kind == "turn_started":
        updated["current_turn_id"] = _clean_text(event.get("turn_id"))
        updated["status"] = "running"

    if runtime_binding_id:
        updated["runtime_binding_id"] = runtime_binding_id

    if created_at:
        if not _clean_text(updated.get("created_at")):
            updated["created_at"] = created_at
        updated["updated_at"] = created_at

    if not _clean_text(updated.get("updated_at")):
        updated["updated_at"] = _clean_text(updated.get("created_at"))

    return updated


def fold_session_events(
    session_id: str, events: Iterable[Mapping[str, object]]
) -> dict[str, object]:
    projection = _base_session_projection(session_id)
    for event in events:
        projection = _apply_session_event(projection, event)
    return projection


class CodingSessionLedger:
    async def create_session(
        self,
        *,
        session_id: str,
        workspace_id: str,
        repo_root: str,
        backend: str,
        transport: str,
        created_at: str = "",
    ) -> dict[str, object]:
        session_key = _clean_text(session_id)
        if not session_key:
            raise ValueError("session_id is required")

        lock = _lock_for(session_key)
        async with lock:
            ensure_coding_session_root(session_key)
            events_path = coding_session_events_path(session_key)
            session_path = coding_session_path(session_key)
            if not events_path.exists():
                events_path.write_text("", encoding="utf-8")

            existing_events = _read_events(events_path)
            if existing_events:
                if not any(
                    _clean_text(event.get("kind")) == "session_created"
                    for event in existing_events
                ):
                    created_event = _normalize_event(
                        session_id=session_key,
                        event={
                            "kind": "session_created",
                            "source": "ikaros",
                            "workspace_id": _clean_text(workspace_id),
                            "repo_root": _clean_text(repo_root),
                            "backend": _clean_text(backend),
                            "transport": _clean_text(transport),
                            "status": "running",
                            "created_at": _clean_text(created_at) or _now_iso(),
                        },
                    )
                    _append_jsonl(events_path, created_event)
                    existing_events = [*existing_events, created_event]
                projection = fold_session_events(session_key, existing_events)
                _write_json(session_path, projection)
                return projection

            created_event = _normalize_event(
                session_id=session_key,
                event={
                    "kind": "session_created",
                    "source": "ikaros",
                    "workspace_id": _clean_text(workspace_id),
                    "repo_root": _clean_text(repo_root),
                    "backend": _clean_text(backend),
                    "transport": _clean_text(transport),
                    "status": "running",
                    "created_at": _clean_text(created_at) or _now_iso(),
                },
            )
            _append_jsonl(events_path, created_event)
            projection = fold_session_events(session_key, [created_event])
            _write_json(session_path, projection)
            return projection

    async def append_event(
        self, *, session_id: str, event: Mapping[str, object]
    ) -> dict[str, object]:
        session_key = _clean_text(session_id)
        if not session_key:
            raise ValueError("session_id is required")

        lock = _lock_for(session_key)
        async with lock:
            ensure_coding_session_root(session_key)
            events_path = coding_session_events_path(session_key)
            session_path = coding_session_path(session_key)
            if not events_path.exists():
                events_path.write_text("", encoding="utf-8")

            existing_events = _read_events(events_path)
            normalized = _normalize_event(session_id=session_key, event=event)
            duplicate = _find_duplicate(existing_events, normalized)
            if duplicate is not None:
                _write_json(
                    session_path, fold_session_events(session_key, existing_events)
                )
                return duplicate

            _append_jsonl(events_path, normalized)
            projection = fold_session_events(
                session_key, [*existing_events, normalized]
            )
            _write_json(session_path, projection)
            return normalized

    async def list_events(self, session_id: str) -> list[dict[str, object]]:
        session_key = _clean_text(session_id)
        if not session_key:
            raise ValueError("session_id is required")

        lock = _lock_for(session_key)
        async with lock:
            return _read_events(coding_session_events_path(session_key))

    async def load_session(self, session_id: str) -> dict[str, object] | None:
        session_key = _clean_text(session_id)
        if not session_key:
            raise ValueError("session_id is required")

        lock = _lock_for(session_key)
        async with lock:
            loaded = _read_json_dict(coding_session_path(session_key))
            if loaded is not None:
                return loaded

            events = _read_events(coding_session_events_path(session_key))
            if not events:
                return None

            projection = fold_session_events(session_key, events)
            _write_json(coding_session_path(session_key), projection)
            return projection

    async def rebuild_session(self, session_id: str) -> dict[str, object] | None:
        session_key = _clean_text(session_id)
        if not session_key:
            raise ValueError("session_id is required")

        lock = _lock_for(session_key)
        async with lock:
            events = _read_events(coding_session_events_path(session_key))
            if not events:
                return None
            projection = fold_session_events(session_key, events)
            _write_json(coding_session_path(session_key), projection)
            return projection
