import json

import pytest

from manager.dev.coding_session_ledger import CodingSessionLedger
from manager.dev.session_paths import (
    coding_session_events_path,
    coding_session_path,
    coding_session_root,
)


@pytest.mark.asyncio
async def test_coding_session_ledger_creates_expected_files_and_dedupes_events(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    ledger = CodingSessionLedger()
    event_created_at = "2026-03-16T00:00:00+08:00"

    created = await ledger.create_session(
        session_id="cs-1",
        workspace_id="ws-1",
        repo_root="/repo",
        backend="opencode",
        transport="acp",
    )
    first_event = await ledger.append_event(
        session_id="cs-1",
        event={
            "source": "acp",
            "source_event_id": "evt-1",
            "kind": "turn_started",
            "turn_id": "turn-1",
            "runtime_binding_id": "rt-1",
            "created_at": event_created_at,
        },
    )
    duplicate_event = await ledger.append_event(
        session_id="cs-1",
        event={
            "source": "acp",
            "source_event_id": "evt-1",
            "kind": "turn_started",
            "turn_id": "turn-1",
            "runtime_binding_id": "rt-1",
            "created_at": event_created_at,
        },
    )

    root = coding_session_root("cs-1")
    session = await ledger.load_session("cs-1")
    events = await ledger.list_events("cs-1")

    assert root == (tmp_path / "data" / "system" / "coding_sessions" / "cs-1").resolve()
    assert coding_session_path("cs-1").exists()
    assert coding_session_events_path("cs-1").exists()
    assert created["session_id"] == "cs-1"
    assert created["workspace_id"] == "ws-1"
    assert created["repo_root"] == "/repo"
    assert created["backend"] == "opencode"
    assert created["transport"] == "acp"
    assert created["status"] == "running"
    assert created["created_at"]
    assert created["updated_at"]
    assert first_event["source"] == "acp"
    assert first_event["source_event_id"] == "evt-1"
    assert first_event["turn_id"] == "turn-1"
    assert first_event["created_at"] == event_created_at
    assert duplicate_event["event_id"] == first_event["event_id"]
    assert session["current_turn_id"] == "turn-1"
    assert session["runtime_binding_id"] == "rt-1"
    assert len(events) == 2
    assert events[0]["kind"] == "session_created"
    assert events[1]["kind"] == "turn_started"
    assert events[1]["source"] == "acp"
    assert events[1]["source_event_id"] == "evt-1"
    assert events[1]["turn_id"] == "turn-1"
    assert events[1]["runtime_binding_id"] == "rt-1"
    assert events[1]["created_at"] == event_created_at


@pytest.mark.asyncio
async def test_coding_session_ledger_rebuilds_projection_from_events(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    ledger = CodingSessionLedger()

    await ledger.create_session(
        session_id="cs-2",
        workspace_id="ws-2",
        repo_root="/repo-two",
        backend="opencode",
        transport="acp",
    )
    await ledger.append_event(
        session_id="cs-2",
        event={
            "source": "acp",
            "source_event_id": "evt-2",
            "kind": "turn_started",
            "turn_id": "turn-2",
            "runtime_binding_id": "rt-2",
        },
    )

    session_path = coding_session_path("cs-2")
    session_path.unlink()

    rebuilt = await ledger.rebuild_session("cs-2")
    persisted = json.loads(session_path.read_text(encoding="utf-8"))

    assert rebuilt["session_id"] == "cs-2"
    assert rebuilt["workspace_id"] == "ws-2"
    assert rebuilt["repo_root"] == "/repo-two"
    assert rebuilt["backend"] == "opencode"
    assert rebuilt["transport"] == "acp"
    assert rebuilt["status"] == "running"
    assert rebuilt["current_turn_id"] == "turn-2"
    assert rebuilt["runtime_binding_id"] == "rt-2"
    assert persisted == rebuilt


@pytest.mark.asyncio
async def test_coding_session_ledger_create_session_backfills_session_created_event(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    ledger = CodingSessionLedger()

    await ledger.append_event(
        session_id="cs-3",
        event={
            "source": "acp",
            "source_event_id": "evt-3",
            "kind": "turn_started",
            "turn_id": "turn-3",
        },
    )

    created = await ledger.create_session(
        session_id="cs-3",
        workspace_id="ws-3",
        repo_root="/repo-three",
        backend="opencode",
        transport="acp",
    )
    events = await ledger.list_events("cs-3")

    assert created["workspace_id"] == "ws-3"
    assert created["repo_root"] == "/repo-three"
    assert created["backend"] == "opencode"
    assert created["transport"] == "acp"
    assert created["current_turn_id"] == "turn-3"
    assert len(events) == 2
    assert [event["kind"] for event in events].count("session_created") == 1


@pytest.mark.asyncio
async def test_coding_session_ledger_duplicate_event_rebuilds_projection(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    ledger = CodingSessionLedger()

    await ledger.create_session(
        session_id="cs-4",
        workspace_id="ws-4",
        repo_root="/repo-four",
        backend="opencode",
        transport="acp",
    )
    await ledger.append_event(
        session_id="cs-4",
        event={
            "source": "acp",
            "source_event_id": "evt-4",
            "kind": "turn_started",
            "turn_id": "turn-4",
            "created_at": "2026-03-16T01:00:00+08:00",
        },
    )

    session_path = coding_session_path("cs-4")
    session_path.write_text(
        json.dumps({"session_id": "cs-4", "current_turn_id": "stale"}) + "\n",
        encoding="utf-8",
    )

    duplicate = await ledger.append_event(
        session_id="cs-4",
        event={
            "source": "acp",
            "source_event_id": "evt-4",
            "kind": "turn_started",
            "turn_id": "turn-4",
            "created_at": "2026-03-16T01:00:00+08:00",
        },
    )
    rebuilt = json.loads(session_path.read_text(encoding="utf-8"))

    assert duplicate["source_event_id"] == "evt-4"
    assert rebuilt["session_id"] == "cs-4"
    assert rebuilt["workspace_id"] == "ws-4"
    assert rebuilt["current_turn_id"] == "turn-4"


@pytest.mark.asyncio
async def test_coding_session_ledger_rebuild_returns_none_without_events(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    ledger = CodingSessionLedger()
    root = coding_session_root("cs-empty")
    session_path = coding_session_path("cs-empty")
    events_path = coding_session_events_path("cs-empty")

    assert not root.exists()
    assert not session_path.exists()
    assert not events_path.exists()

    rebuilt = await ledger.rebuild_session("cs-empty")

    assert rebuilt is None
    assert not root.exists()
    assert not session_path.exists()
    assert not events_path.exists()
