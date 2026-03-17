# OpenCode Manager ACP Coding Runtime Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manager-only OpenCode ACP coding runtime with Manager-owned ledger state, workspace-scoped sidecars, checkpoint-based recovery, and limited CLI fallback.

**Architecture:** Introduce a neutral `coding_session` layer under `src/manager/dev/` and make it the canonical owner of coding-session persistence, approvals, snapshots, runtime event folding, and recovery. OpenCode runs only as an out-of-process ACP sidecar supervised by Manager; existing `codex_session` remains a compatibility facade that forwards into the new service while legacy readers keep working during migration.

**Tech Stack:** Python 3.14, asyncio, JSON/JSONL file-backed state, subprocess sidecars, git snapshots, pytest, pytest-asyncio

---

Spec: `docs/superpowers/specs/2026-03-15-opencode-manager-acp-coding-runtime-design.md`

## File Map

- Create: `src/manager/dev/coding_session_ledger.py` - append-only ledger, event dedupe, session projection, turn/checkpoint persistence.
- Create: `src/manager/dev/repo_snapshot_service.py` - staged/unstaged patch artifacts, untracked manifests, binary/oversized metadata records.
- Create: `src/manager/dev/coding_session_service.py` - canonical manager-owned coding session facade.
- Create: `src/manager/dev/runtimes/base.py` - runtime binding dataclasses, event types, adapter protocol.
- Create: `src/manager/dev/runtimes/opencode_sidecar_supervisor.py` - workspace-scoped `opencode acp` process supervision.
- Create: `src/manager/dev/runtimes/opencode_acp_adapter.py` - ACP JSON-RPC mapping into Manager events.
- Create: `src/manager/dev/runtimes/cli_adapter.py` - bounded noninteractive fallback adapter.
- Modify: `src/manager/dev/session_paths.py` - coding-session roots, artifact paths, runtime binding paths.
- Modify: `src/manager/dev/runtime.py` - preserve `opencode` as a distinct CLI backend and keep low-level subprocess helpers reusable.
- Modify: `src/manager/dev/codex_session_service.py` - compatibility shim into `CodingSessionService`.
- Modify: `src/manager/relay/result_relay.py` - compatibility lookup for recent auto-repair sessions.
- Modify: `src/manager/dispatch/service.py` - central rejection for worker coding backends.
- Modify: `src/main.py` - bootstrap recovery scan for recoverable manager-owned coding sessions.
- Modify: `src/core/tools/codex_tools.py` - keep public tool name stable while delegating to the new service.
- Modify: `src/core/skill_tool_handlers.py` - preserve manager tool surface and accept `opencode` backend token.
- Modify: `src/core/tool_access_store.py` - deny worker coding backends, including OpenCode tokens.
- Modify: `src/handlers/worker_handlers.py` - reject worker coding backends in the `/worker backend` command.
- Modify: `skills/builtin/skill_manager/scripts/execute.py` - preserve `opencode` backend instead of collapsing to `codex`.
- Modify: `skills/builtin/worker_management/scripts/execute.py` - align worker-dispatch script behavior with central backend rejection.
- Test: `tests/manager/test_coding_session_ledger.py`
- Test: `tests/manager/test_repo_snapshot_service.py`
- Test: `tests/manager/test_opencode_sidecar_supervisor.py`
- Test: `tests/manager/test_opencode_acp_adapter.py`
- Test: `tests/manager/test_cli_adapter.py`
- Test: `tests/manager/test_coding_session_service.py`
- Test: `tests/manager/test_coding_session_recovery.py`
- Modify Test: `tests/manager/test_codex_session_service.py`
- Modify Test: `tests/manager/test_dev_runtime.py`
- Modify Test: `tests/manager/test_dispatch_service.py`
- Modify Test: `tests/core/test_worker_result_relay.py`
- Modify Test: `tests/core/test_main_bootstrap.py`
- Modify Test: `tests/core/test_tool_access_store.py`
- Modify Test: `tests/core/test_worker_handlers_filters.py`
- Modify Test: `tests/core/test_worker_management_execute.py`
- Modify Test: `tests/core/test_skill_tool_handlers.py`
- Modify Test: `tests/core/test_skill_manager_coding_modes.py`

## Chunk 1: Ledger And Snapshot Foundations

### Task 1: Add the canonical coding-session ledger

**Files:**
- Create: `src/manager/dev/coding_session_ledger.py`
- Modify: `src/manager/dev/session_paths.py`
- Test: `tests/manager/test_coding_session_ledger.py`

- [ ] **Step 1: Write failing ledger tests for directory layout, append-only events, and dedupe-safe projections**

```python
@pytest.mark.asyncio
async def test_coding_session_ledger_creates_expected_files_and_dedupes_events(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    ledger = CodingSessionLedger()

    await ledger.create_session(
        session_id="cs-1",
        workspace_id="ws-1",
        repo_root="/repo",
        backend="opencode",
        transport="acp",
    )
    await ledger.append_event(
        session_id="cs-1",
        event={
            "source": "acp",
            "source_event_id": "evt-1",
            "kind": "turn_started",
            "turn_id": "turn-1",
        },
    )
    await ledger.append_event(
        session_id="cs-1",
        event={
            "source": "acp",
            "source_event_id": "evt-1",
            "kind": "turn_started",
            "turn_id": "turn-1",
        },
    )

    root = coding_session_root("cs-1")
    session = await ledger.load_session("cs-1")
    events = await ledger.list_events("cs-1")

    assert (root / "session.json").exists()
    assert (root / "events.jsonl").exists()
    assert session["current_turn_id"] == "turn-1"
    assert len(events) == 1
```

- [ ] **Step 2: Run the focused ledger tests to verify they fail**

Run: `uv run pytest tests/manager/test_coding_session_ledger.py -q`
Expected: FAIL because the coding-session paths and ledger module do not exist.

- [ ] **Step 3: Implement coding-session path helpers and append-only event persistence**

```python
def coding_session_root(session_id: str) -> Path:
    root = (_system_root() / "coding_sessions" / safe_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


async def append_event(self, *, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    normalized = self._normalize_event(session_id=session_id, event=event)
    self._append_jsonl(events_path, normalized)
    await self._refresh_projection(session_id)
    return normalized
```

- [ ] **Step 4: Add dedupe-key handling and deterministic projection folding**

```python
dedupe_key = self._dedupe_key(normalized)
if dedupe_key and dedupe_key in seen_keys:
    return normalized
seen_keys.add(dedupe_key)
```

- [ ] **Step 5: Re-run the focused ledger tests**

Run: `uv run pytest tests/manager/test_coding_session_ledger.py -q`
Expected: PASS

- [ ] **Step 6: Commit the ledger foundation**

```bash
git add src/manager/dev/coding_session_ledger.py src/manager/dev/session_paths.py tests/manager/test_coding_session_ledger.py
git commit -m "feat: add coding session ledger foundation"
```

### Task 2: Add repository snapshots and checkpoint references

**Files:**
- Create: `src/manager/dev/repo_snapshot_service.py`
- Modify: `src/manager/dev/session_paths.py`
- Modify: `src/manager/dev/coding_session_ledger.py`
- Test: `tests/manager/test_repo_snapshot_service.py`
- Modify Test: `tests/manager/test_coding_session_ledger.py`

- [ ] **Step 1: Write a failing snapshot test that creates a real git repo with staged, unstaged, untracked, and binary changes**

```python
@pytest.mark.asyncio
async def test_repo_snapshot_service_writes_patch_manifest_and_binary_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=repo_root, check=True)
    (repo_root / "tracked.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_root, check=True)

    (repo_root / "tracked.txt").write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo_root, check=True)
    (repo_root / "tracked.txt").write_text("v3\n", encoding="utf-8")
    (repo_root / "new.txt").write_text("untracked\n", encoding="utf-8")
    (repo_root / "image.bin").write_bytes(b"\x00\x01\x02")

    snapshot = await RepoSnapshotService().capture(
        session_id="cs-1",
        workspace_id="ws-1",
        repo_root=str(repo_root),
    )

    assert snapshot["staged_diff_artifact_id"]
    assert snapshot["unstaged_diff_artifact_id"]
    assert snapshot["untracked_manifest_artifact_id"]
```

- [ ] **Step 2: Run the snapshot test to verify it fails for the intended reason**

Run: `uv run pytest tests/manager/test_repo_snapshot_service.py::test_repo_snapshot_service_writes_patch_manifest_and_binary_artifacts -q`
Expected: FAIL because `RepoSnapshotService` and artifact path helpers do not exist, not because the repo fixture is incomplete.

- [ ] **Step 3: Implement snapshot path helpers and staged/unstaged/untracked artifact capture**

```python
return {
    "workspace_id": workspace_id,
    "repo_root": repo_root,
    "branch": branch_name,
    "head": head_sha,
    "worktree": worktree_name,
    "staged_diff_artifact_id": staged_artifact_id,
    "unstaged_diff_artifact_id": unstaged_artifact_id,
    "untracked_manifest_artifact_id": manifest_artifact_id,
    "snapshot_at": _now_iso(),
}
```

- [ ] **Step 4: Re-run the focused snapshot test**

Run: `uv run pytest tests/manager/test_repo_snapshot_service.py::test_repo_snapshot_service_writes_patch_manifest_and_binary_artifacts -q`
Expected: PASS for staged/unstaged/untracked capture.

- [ ] **Step 5: Write a second failing snapshot test for detached-head, worktree identity, and binary metadata content**

```python
@pytest.mark.asyncio
async def test_repo_snapshot_service_records_detached_head_worktree_and_binary_metadata(tmp_path, monkeypatch):
    ...
    subprocess.run(["git", "worktree", "add", str(linked_root), "-b", "feature/ws-1"], cwd=repo_root, check=True)
    subprocess.run(["git", "checkout", "--detach"], cwd=linked_root, check=True)
    (linked_root / "image.bin").write_bytes(b"\x00\x01\x02")

    snapshot = await RepoSnapshotService().capture(..., repo_root=str(linked_root))

    assert snapshot["worktree"]
    assert snapshot["is_detached_head"] is True
    assert snapshot["binary_metadata_artifact_ids"]
```

- [ ] **Step 6: Run the detached-head/worktree snapshot test to verify it fails**

Run: `uv run pytest tests/manager/test_repo_snapshot_service.py::test_repo_snapshot_service_records_detached_head_worktree_and_binary_metadata -q`
Expected: FAIL because detached-head/worktree fields and binary metadata contents are not yet persisted.

- [ ] **Step 7: Extend the snapshot payload with binary metadata and detached-head/worktree identity fields**

```python
return {
    ...
    "worktree": worktree_name,
    "is_detached_head": detached_head,
    "binary_metadata_artifact_ids": binary_artifact_ids,
}
```

- [ ] **Step 8: Re-run both snapshot tests**

Run: `uv run pytest tests/manager/test_repo_snapshot_service.py -q`
Expected: PASS

- [ ] **Step 9: Write a failing ledger test that checkpoints persist repo-snapshot references**

```python
@pytest.mark.asyncio
async def test_coding_session_ledger_persists_checkpoint_snapshot_refs(...):
    checkpoint = await ledger.write_checkpoint(
        session_id="cs-1",
        repo_snapshot=snapshot,
        resume_prompt="resume from checkpoint",
    )
    assert checkpoint["repo_snapshot"]["staged_diff_artifact_id"]
    assert checkpoint["repo_snapshot"]["binary_metadata_artifact_ids"]
```

- [ ] **Step 10: Run the checkpoint test to verify it fails for the new linkage contract**

Run: `uv run pytest tests/manager/test_coding_session_ledger.py::test_coding_session_ledger_persists_checkpoint_snapshot_refs -q`
Expected: FAIL because checkpoint writing does not yet persist repo-snapshot references.

- [ ] **Step 11: Implement checkpoint persistence with repo-snapshot linkage**

```python
checkpoint = {
    "checkpoint_id": checkpoint_id,
    "resume_prompt": resume_prompt,
    "repo_snapshot": snapshot,
}
```

- [ ] **Step 12: Re-run the snapshot and checkpoint tests**

Run: `uv run pytest tests/manager/test_repo_snapshot_service.py tests/manager/test_coding_session_ledger.py -q`
Expected: PASS

- [ ] **Step 13: Commit the snapshot layer**

```bash
git add src/manager/dev/repo_snapshot_service.py src/manager/dev/session_paths.py src/manager/dev/coding_session_ledger.py tests/manager/test_repo_snapshot_service.py tests/manager/test_coding_session_ledger.py
git commit -m "feat: add coding session snapshot artifacts"
```

## Chunk 2: Runtime Bindings And Adapters

### Task 3: Add the runtime contract and sidecar supervisor

**Files:**
- Create: `src/manager/dev/runtimes/base.py`
- Create: `src/manager/dev/runtimes/opencode_sidecar_supervisor.py`
- Modify: `src/manager/dev/session_paths.py`
- Test: `tests/manager/test_opencode_sidecar_supervisor.py`

- [ ] **Step 1: Write failing supervisor tests with a fake subprocess for spawn contract, binding reuse, and exit reporting**

```python
class _FakeProcess:
    def __init__(self):
        self.pid = 1234
        self.returncode = None

    async def wait(self):
        self.returncode = 9
        return 9


@pytest.mark.asyncio
async def test_sidecar_supervisor_spawns_opencode_acp_once_per_workspace(monkeypatch, tmp_path):
    spawned = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        spawned.append((args, kwargs))
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    supervisor = OpenCodeSidecarSupervisor()

    first = await supervisor.ensure_runtime("ws-1", str(tmp_path))
    second = await supervisor.ensure_runtime("ws-1", str(tmp_path))

    assert first["runtime_binding_id"] == second["runtime_binding_id"]
    assert spawned[0][0][:4] == ("opencode", "acp", "--cwd", str(tmp_path))
    assert spawned[0][1]["stdout"] is asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_sidecar_supervisor_emits_runtime_exit_event(monkeypatch, tmp_path):
    reported = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        _ = (args, kwargs)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    supervisor = OpenCodeSidecarSupervisor(on_runtime_exit=reported.append)
    binding = await supervisor.ensure_runtime("ws-1", str(tmp_path))
    await supervisor._watch_process(binding["runtime_binding_id"])

    assert reported[0]["kind"] == "runtime_exited"
    assert reported[0]["runtime_binding_id"] == binding["runtime_binding_id"]
```

- [ ] **Step 2: Run the supervisor tests to verify they fail**

Run: `uv run pytest tests/manager/test_opencode_sidecar_supervisor.py -q`
Expected: FAIL because the runtime contract and sidecar supervisor do not exist.

- [ ] **Step 3: Implement the shared runtime protocol and runtime-binding dataclass**

```python
@dataclass(slots=True)
class RuntimeBinding:
    runtime_binding_id: str
    workspace_id: str
    repo_root: str
    transport: str
    pid: int | None = None


@dataclass(slots=True)
class RuntimeEvent:
    kind: str
    session_id: str
    turn_id: str
    runtime_binding_id: str
    source: str
    source_event_id: str
    payload: dict[str, Any]


class CodingRuntimeAdapter(Protocol):
    async def open_session(self, session_id: str, runtime_binding: RuntimeBinding) -> dict[str, Any]: ...
    async def send_turn(self, session_id: str, turn_id: str, prompt: str, context_refs: list[dict[str, Any]], checkpoint_id: str) -> dict[str, Any]: ...
    async def submit_permission_decision(self, session_id: str, permission_id: str, decision: str) -> dict[str, Any]: ...
    async def submit_user_reply(self, session_id: str, turn_id: str, text: str) -> dict[str, Any]: ...
    async def cancel_session(self, session_id: str) -> dict[str, Any]: ...
    async def handle_runtime_exit(self, runtime_binding_id: str, reason: str) -> dict[str, Any]: ...
```

- [ ] **Step 4: Implement sidecar spawn, binding reuse, and runtime-exit reporting**

```python
proc = await asyncio.create_subprocess_exec(
    "opencode", "acp", "--cwd", repo_root, ...
)
```

- [ ] **Step 5: Re-run the supervisor tests**

Run: `uv run pytest tests/manager/test_opencode_sidecar_supervisor.py -q`
Expected: PASS

- [ ] **Step 6: Commit the supervisor slice**

```bash
git add src/manager/dev/runtimes/base.py src/manager/dev/runtimes/opencode_sidecar_supervisor.py src/manager/dev/session_paths.py tests/manager/test_opencode_sidecar_supervisor.py
git commit -m "feat: add opencode sidecar supervisor"
```

### Task 4: Implement the OpenCode ACP adapter

**Files:**
- Create: `src/manager/dev/runtimes/opencode_acp_adapter.py`
- Modify: `src/manager/dev/runtimes/base.py`
- Test: `tests/manager/test_opencode_acp_adapter.py`

- [ ] **Step 1: Write failing adapter tests for message chunks, tool-call events, permission requests, completion mapping, and dedupe keys**

```python
@pytest.mark.asyncio
async def test_acp_adapter_maps_permission_requests_into_manager_events():
    adapter = OpenCodeACPAdapter(...)
    updates = await adapter.handle_notification(
        {
            "method": "session/request_permission",
            "params": {
                "sessionId": "cs-1",
                "toolCall": {"toolCallId": "tool-1", "title": "Edit file"},
                "options": [{"optionId": "allow-once", "kind": "allow_once"}],
            },
        }
    )
    assert updates[0]["kind"] == "permission_requested"
    assert updates[0]["permission_id"]


@pytest.mark.asyncio
async def test_acp_adapter_maps_message_and_completion_updates(...):
    updates = await adapter.handle_notification(...)
    assert [item["kind"] for item in updates] == ["agent_message_chunk", "turn_completed"]


@pytest.mark.asyncio
async def test_acp_adapter_maps_tool_call_lifecycle_and_dedupes_duplicate_event_ids(...):
    first = await adapter.handle_notification(...)
    second = await adapter.handle_notification(...)
    assert [item["kind"] for item in first] == ["tool_call_started", "tool_call_completed"]
    assert second == []
```

- [ ] **Step 2: Run the adapter tests to verify they fail**

Run: `uv run pytest tests/manager/test_opencode_acp_adapter.py -q`
Expected: FAIL because the ACP adapter does not exist.

- [ ] **Step 3: Implement JSON-RPC mapping into runtime-neutral manager events**

```python
if method == "session/update":
    return [self._map_session_update(params)]
if method == "session/request_permission":
    return [self._map_permission_request(params)]
```

- [ ] **Step 4: Add duplicate suppression using `(source, source_event_id)` or deterministic fallback keys**

```python
event["source"] = "acp"
event["source_event_id"] = params.get("eventId") or self._fallback_event_key(event)
```

- [ ] **Step 5: Re-run the adapter tests**

Run: `uv run pytest tests/manager/test_opencode_acp_adapter.py -q`
Expected: PASS

- [ ] **Step 6: Commit the ACP adapter**

```bash
git add src/manager/dev/runtimes/opencode_acp_adapter.py src/manager/dev/runtimes/base.py tests/manager/test_opencode_acp_adapter.py
git commit -m "feat: add opencode acp adapter"
```

### Task 5: Add the limited OpenCode CLI fallback adapter

**Files:**
- Create: `src/manager/dev/runtimes/cli_adapter.py`
- Modify: `src/manager/dev/runtime.py`
- Test: `tests/manager/test_cli_adapter.py`
- Modify Test: `tests/manager/test_dev_runtime.py`

- [ ] **Step 1: Write failing tests for OpenCode CLI command building and bounded noninteractive turns**

```python
def test_runtime_builds_opencode_cli_command(monkeypatch):
    monkeypatch.delenv("CODING_BACKEND_OPENCODE_ARGS_TEMPLATE", raising=False)
    cmd, args = runtime_module._build_coding_command("opencode", "do something")
    assert cmd == "opencode"
    assert "exec" in args


@pytest.mark.asyncio
async def test_cli_adapter_rejects_non_preapproved_turns(monkeypatch):
    adapter = CLIAdapter()
    result = await adapter.send_turn(..., approval_mode="ask")
    assert result["ok"] is False
    assert result["error_code"] == "interactive_cli_not_supported"
```

- [ ] **Step 2: Run the CLI adapter tests to verify they fail**

Run: `uv run pytest tests/manager/test_cli_adapter.py tests/manager/test_dev_runtime.py -q`
Expected: FAIL because `opencode` is still normalized to `codex` and the CLI adapter does not exist.

- [ ] **Step 3: Teach runtime command building to preserve `opencode` instead of collapsing it to `codex`**

```python
if token in {"opencode", "opencode-cli"}:
    return "opencode"
```

- [ ] **Step 4: Implement a thin adapter over `run_exec` / `run_coding_backend` with explicit noninteractive guards**

```python
if approval_mode != "preapproved":
    return {"ok": False, "error_code": "interactive_cli_not_supported"}
```

- [ ] **Step 5: Re-run the CLI adapter tests**

Run: `uv run pytest tests/manager/test_cli_adapter.py tests/manager/test_dev_runtime.py -q`
Expected: PASS

- [ ] **Step 6: Commit the CLI fallback adapter**

```bash
git add src/manager/dev/runtimes/cli_adapter.py src/manager/dev/runtime.py tests/manager/test_cli_adapter.py tests/manager/test_dev_runtime.py
git commit -m "feat: add noninteractive opencode cli fallback"
```

## Chunk 3: Service Integration, Recovery, And Boundaries

### Task 6: Introduce `CodingSessionService` and keep compatibility surfaces alive

**Files:**
- Create: `src/manager/dev/coding_session_service.py`
- Modify: `src/manager/dev/codex_session_service.py`
- Modify: `src/manager/relay/result_relay.py`
- Modify: `src/core/tools/codex_tools.py`
- Modify: `src/core/skill_tool_handlers.py`
- Modify: `skills/builtin/skill_manager/scripts/execute.py`
- Test: `tests/manager/test_coding_session_service.py`
- Modify Test: `tests/manager/test_codex_session_service.py`
- Modify Test: `tests/core/test_worker_result_relay.py`
- Modify Test: `tests/core/test_skill_tool_handlers.py`
- Modify Test: `tests/core/test_skill_manager_coding_modes.py`

- [ ] **Step 1: Write failing service tests for explicit transport routing, single active session per workspace, and compatibility forwarding**

```python
@pytest.mark.asyncio
async def test_coding_session_service_routes_opencode_to_acp_and_codex_to_cli(...):
    created = await service.start(workspace_id="ws-1", instruction="first", backend="opencode")
    assert created["data"]["transport"] == "acp"


@pytest.mark.asyncio
async def test_coding_session_service_rejects_second_active_session_for_workspace(...):
    created = await service.start(workspace_id="ws-1", instruction="first", backend="opencode")
    assert created["ok"] is True

    blocked = await service.start(workspace_id="ws-1", instruction="second", backend="opencode")
    assert blocked["ok"] is False
    assert blocked["error_code"] == "workspace_session_busy"
```

- [ ] **Step 2: Write failing compatibility tests for legacy relay lookups and skill-manager backend normalization**

```python
def test_skill_manager_resolves_opencode_backend(monkeypatch):
    ...
    assert result["used_backend"] == "opencode"


@pytest.mark.asyncio
async def test_recent_auto_repair_exists_reads_compat_session_projection(...):
    ...
    assert await WorkerResultRelay._recent_auto_repair_exists("demo-skill") is True
```

- [ ] **Step 3: Run the service and compatibility tests to verify they fail**

Run: `uv run pytest tests/manager/test_coding_session_service.py tests/manager/test_codex_session_service.py tests/core/test_skill_tool_handlers.py tests/core/test_skill_manager_coding_modes.py tests/core/test_worker_result_relay.py -q`
Expected: FAIL because the neutral coding-session service does not exist, `codex_session` still owns session state, relay compatibility still depends on legacy single-file output, and skill-manager still collapses `opencode` to `codex`.

- [ ] **Step 4: Implement the manager-owned service and forward legacy entrypoints into it**

```python
async def handle(...):
    if action == "start":
        return await self.start(...)
    if action == "continue":
        return await self.continue_session(...)
```

- [ ] **Step 5: Preserve `codex_session` as the public tool name while writing a compatibility projection for legacy readers**

```python
backend = str(tool_args.get("backend") or "codex")
if backend in {"opencode", "opencode-acp"}:
    backend = "opencode"

await self._write_compat_projection(session_id=session_id, payload=session_projection)
```

- [ ] **Step 6: Re-run the service and compatibility tests**

Run: `uv run pytest tests/manager/test_coding_session_service.py tests/manager/test_codex_session_service.py tests/core/test_skill_tool_handlers.py tests/core/test_skill_manager_coding_modes.py tests/core/test_worker_result_relay.py -q`
Expected: PASS

- [ ] **Step 7: Commit the service integration**

```bash
git add src/manager/dev/coding_session_service.py src/manager/dev/codex_session_service.py src/manager/relay/result_relay.py src/core/tools/codex_tools.py src/core/skill_tool_handlers.py skills/builtin/skill_manager/scripts/execute.py tests/manager/test_coding_session_service.py tests/manager/test_codex_session_service.py tests/core/test_worker_result_relay.py tests/core/test_skill_tool_handlers.py tests/core/test_skill_manager_coding_modes.py
git commit -m "refactor: route codex session through coding session service"
```

### Task 7: Implement Manager-led recovery and bootstrap resume scanning

**Files:**
- Modify: `src/manager/dev/coding_session_service.py`
- Modify: `src/manager/dev/coding_session_ledger.py`
- Modify: `src/manager/dev/repo_snapshot_service.py`
- Modify: `src/manager/dev/runtimes/opencode_sidecar_supervisor.py`
- Modify: `src/main.py`
- Test: `tests/manager/test_coding_session_recovery.py`
- Modify Test: `tests/core/test_main_bootstrap.py`

- [ ] **Step 1: Write failing recovery tests for sidecar crash, normal checkpoint creation, and Manager restart**

```python
@pytest.mark.asyncio
async def test_coding_session_service_writes_checkpoint_after_successful_turn(...):
    session = await service.start(...)
    checkpoint = await ledger.latest_checkpoint(session["data"]["session_id"])
    assert checkpoint["repo_snapshot"]["staged_diff_artifact_id"]


@pytest.mark.asyncio
async def test_coding_session_service_recovers_from_sidecar_exit_with_new_turn(...):
    recovered = await service.handle_runtime_exit(...)
    assert recovered["data"]["status"] == "recovering"
    assert recovered["data"]["current_turn_id"] != original_turn_id


@pytest.mark.asyncio
async def test_manager_restart_ignores_surviving_sidecar_and_recovers_from_checkpoint(...):
    session = await ledger.load_session("cs-1")
    assert session["status"] == "recovering"
    assert session["current_turn_id"] == "turn-recovery-1"
```

- [ ] **Step 2: Run the recovery and bootstrap tests to verify they fail**

Run: `uv run pytest tests/manager/test_coding_session_recovery.py tests/core/test_main_bootstrap.py -q`
Expected: FAIL because there is no checkpoint-based recovery flow and bootstrap does not resume recoverable coding sessions.

- [ ] **Step 3: Implement `runtime_exited -> recovering -> new recovery turn` orchestration**

```python
await ledger.append_event(session_id=session_id, event={"kind": "runtime_exited", ...})
await ledger.project_status(session_id, "recovering")
checkpoint = await self._latest_recoverable_checkpoint(session_id)
return await self._start_recovery_turn(session_id=session_id, checkpoint=checkpoint)
```

- [ ] **Step 4: Enforce the Manager-restart hard rule: surviving sidecars are never authoritative**

```python
if self._found_surviving_sidecar(binding):
    await self._discard_runtime_binding(binding)
    return await self._recover_from_checkpoint(session_id)
```

- [ ] **Step 5: Wire recovery scanning into `main.init_services()`**

```python
from manager.dev.coding_session_service import coding_session_service

await coding_session_service.resume_recoverable_sessions()
```

- [ ] **Step 6: Re-run the recovery and bootstrap tests**

Run: `uv run pytest tests/manager/test_coding_session_recovery.py tests/core/test_main_bootstrap.py -q`
Expected: PASS

- [ ] **Step 7: Commit the recovery flow**

```bash
git add src/manager/dev/coding_session_service.py src/manager/dev/coding_session_ledger.py src/manager/dev/repo_snapshot_service.py src/manager/dev/runtimes/opencode_sidecar_supervisor.py src/main.py tests/manager/test_coding_session_recovery.py tests/core/test_main_bootstrap.py
git commit -m "feat: add manager-led coding session recovery"
```

### Task 8: Lock Worker out of coding backends at every entrypoint

**Files:**
- Modify: `src/core/tool_access_store.py`
- Modify: `src/handlers/worker_handlers.py`
- Modify: `src/manager/dispatch/service.py`
- Modify: `skills/builtin/worker_management/scripts/execute.py`
- Modify Test: `tests/core/test_tool_access_store.py`
- Modify Test: `tests/core/test_worker_handlers_filters.py`
- Modify Test: `tests/manager/test_dispatch_service.py`
- Modify Test: `tests/core/test_worker_management_execute.py`

- [ ] **Step 1: Write failing tests for worker backend rejection and policy denial of OpenCode at both UI and dispatch layers**

```python
def test_tool_access_worker_policy_denies_opencode_backend(tmp_path):
    store = ToolAccessStore()
    ...
    allowed, _ = store.is_backend_allowed(worker_id="worker-main", backend="opencode")
    assert allowed is False


async def test_worker_backend_command_rejects_coding_backends(monkeypatch):
    ctx = _FakeCtx("/worker backend worker-main codex")
    await worker_handlers_module.worker_command(ctx)
    assert "不再允许" in ctx.replies[0]


@pytest.mark.asyncio
async def test_manager_dispatch_service_rejects_worker_coding_backend(...):
    result = await manager_dispatch_service.dispatch_worker(
        instruction="run coding task",
        worker_id="worker-main",
        backend="opencode",
    )
    assert result["ok"] is False
    assert result["error_code"] == "worker_coding_backend_forbidden"
```

- [ ] **Step 2: Run the worker-boundary tests to verify they fail**

Run: `uv run pytest tests/core/test_tool_access_store.py tests/core/test_worker_handlers_filters.py tests/manager/test_dispatch_service.py tests/core/test_worker_management_execute.py -q`
Expected: FAIL because worker command handling, worker-dispatch tooling, and dispatch service still permit coding backends in some paths.

- [ ] **Step 3: Remove coding backends from `/worker backend` handling and deny `opencode` in backend-group checks**

```python
if backend in {"codex", "gemini", "gemini-cli", "opencode", "opencode-acp"}:
    await ctx.reply("❌ Worker 不再允许使用编码类 backend；请改用 Manager 的 `codex_session` 能力。")
    return
```

- [ ] **Step 4: Add a central dispatch-service rejection so scripts cannot bypass the UI guard**

```python
if backend_name in {"codex", "gemini-cli", "opencode", "opencode-acp"}:
    return {"ok": False, "error_code": "worker_coding_backend_forbidden", ...}
```

- [ ] **Step 5: Re-run the worker-boundary tests**

Run: `uv run pytest tests/core/test_tool_access_store.py tests/core/test_worker_handlers_filters.py tests/manager/test_dispatch_service.py tests/core/test_worker_management_execute.py -q`
Expected: PASS

- [ ] **Step 6: Commit the worker boundary enforcement**

```bash
git add src/core/tool_access_store.py src/handlers/worker_handlers.py src/manager/dispatch/service.py skills/builtin/worker_management/scripts/execute.py tests/core/test_tool_access_store.py tests/core/test_worker_handlers_filters.py tests/manager/test_dispatch_service.py tests/core/test_worker_management_execute.py
git commit -m "fix: keep worker out of coding backends"
```

## Chunk 4: End-To-End Verification

### Task 9: Run focused regressions and verify the per-task commits left a clean workspace

**Files:**
- Test: `tests/manager/test_coding_session_ledger.py`
- Test: `tests/manager/test_repo_snapshot_service.py`
- Test: `tests/manager/test_opencode_sidecar_supervisor.py`
- Test: `tests/manager/test_opencode_acp_adapter.py`
- Test: `tests/manager/test_cli_adapter.py`
- Test: `tests/manager/test_coding_session_service.py`
- Test: `tests/manager/test_coding_session_recovery.py`
- Test: `tests/manager/test_codex_session_service.py`
- Test: `tests/manager/test_dev_runtime.py`
- Test: `tests/manager/test_dispatch_service.py`
- Test: `tests/core/test_worker_result_relay.py`
- Test: `tests/core/test_main_bootstrap.py`
- Test: `tests/core/test_tool_access_store.py`
- Test: `tests/core/test_worker_handlers_filters.py`
- Test: `tests/core/test_worker_management_execute.py`
- Test: `tests/core/test_skill_tool_handlers.py`
- Test: `tests/core/test_skill_manager_coding_modes.py`

- [ ] **Step 1: Run the focused manager/core regression suite**

Run: `uv run pytest tests/manager/test_coding_session_ledger.py tests/manager/test_repo_snapshot_service.py tests/manager/test_opencode_sidecar_supervisor.py tests/manager/test_opencode_acp_adapter.py tests/manager/test_cli_adapter.py tests/manager/test_coding_session_service.py tests/manager/test_coding_session_recovery.py tests/manager/test_codex_session_service.py tests/manager/test_dev_runtime.py tests/manager/test_dispatch_service.py tests/core/test_worker_result_relay.py tests/core/test_main_bootstrap.py tests/core/test_tool_access_store.py tests/core/test_worker_handlers_filters.py tests/core/test_worker_management_execute.py tests/core/test_skill_tool_handlers.py tests/core/test_skill_manager_coding_modes.py -q`
Expected: PASS

- [ ] **Step 2: Run one broader manager-runtime regression pass**

Run: `uv run pytest tests/core/test_orchestrator_runtime_tools.py tests/core/test_prompt_composer.py -q`
Expected: PASS, confirming manager tool exposure and prompt guidance remain intact.

- [ ] **Step 3: Verify the workspace is clean after the per-task commits**

Run: `git diff --quiet && git diff --cached --quiet && test -z "$(git ls-files --others --exclude-standard)"`
Expected: exit code 0

- [ ] **Step 4: Verify the recent commit stack contains only the planned chunk commits**

Run: `BASE_REF=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's#refs/remotes/##') && BASE_SHA=$(git merge-base HEAD "$BASE_REF") && test "$(git rev-list --count "$BASE_SHA"..HEAD)" = "8" && git log --reverse --format=%s "$BASE_SHA"..HEAD`
Expected:
```text
feat: add coding session ledger foundation
feat: add coding session snapshot artifacts
feat: add opencode sidecar supervisor
feat: add opencode acp adapter
feat: add noninteractive opencode cli fallback
refactor: route codex session through coding session service
feat: add manager-led coding session recovery
fix: keep worker out of coding backends
```
