# OpenCode Manager ACP Coding Runtime Design

> 历史设计文档：文中对独立 Worker 边界的讨论基于旧架构；当前仓库以 Manager-only 运行面为准。

Date: 2026-03-15
Status: Draft reviewed with user in chat
Scope: manager coding sessions, external coding runtime integration, session ledger, ACP adapter, sidecar supervision, recovery

## Background

The current coding backend path is manager-owned and CLI-oriented. Manager starts a coding command, captures stdout and stderr, and stores a single session snapshot under `codex_session` state. That model is adequate for short one-shot runs, but it becomes limiting for a richer coding runtime with structured session updates, explicit permission requests, and resumable long-lived interaction.

At the same time, this repository has a non-negotiable architectural boundary:

- coding authority belongs only to Manager;
- Worker must not participate in coding execution;
- Worker is allowed to be dynamically modified at runtime, so it cannot be the trusted host for coding operations;
- Manager code must remain runtime-stable and must own recovery semantics.

The design therefore needs to integrate OpenCode as an external coding engine without making it the canonical owner of coding-session truth.

## Goals

1. Integrate OpenCode as a manager-only coding backend.
2. Prefer a structured protocol integration over command-output scraping.
3. Keep Manager as the sole owner of canonical session state, approvals, and recovery logic.
4. Make OpenCode replaceable by keeping runtime integration behind a neutral `coding_session` abstraction.
5. Support sidecar crash recovery without trusting sidecar memory as the source of truth.
6. Preserve a CLI fallback path when ACP is unavailable.

## Non-Goals

- Allowing Worker to run coding sessions.
- Hot-modifying Manager at runtime.
- Treating OpenCode session memory as durable truth.
- Designing a universal multi-agent orchestration system for all future runtimes in this change.
- Removing existing codex or gemini-cli support in the first migration step.

## Approaches Considered

### Option A: Pure CLI Execution Adapter

Manager would continue to spawn a subprocess per coding round, now using `opencode exec` or an equivalent command. This is the safest atomic model, but it leaves Manager with poor visibility into intermediate plans, permission requests, tool calls, and streaming progress. Long-lived sessions become a stdout contract rather than a protocol contract.

### Option B: Global ACP Runtime

Manager would maintain one long-lived `opencode acp` process for all coding sessions. This improves protocol structure, but it creates an unnecessarily large failure domain and couples unrelated repositories or workspaces to one external runtime process.

### Option C: Workspace-Scoped ACP Sidecars With Manager-Owned Ledger (Recommended)

Manager supervises one external `opencode acp --cwd <repo_root>` sidecar per active workspace, while all canonical session state lives in Manager-managed ledger files. This preserves process isolation, gives Manager a structured event stream, and makes recovery a Manager responsibility rather than a sidecar responsibility.

In v1, a workspace may have at most one active coding session bound to its sidecar at a time. Historical sessions may reference the same workspace, but concurrent active coding sessions in one workspace are out of scope for the first implementation.

Recommendation: choose Option C and keep Option A as a fallback adapter.

## Design Principles

### Manager Owns Truth, Runtime Emits Facts

OpenCode may emit useful session updates, but those updates are observations. They are not the authoritative record of the session lifecycle. Manager must fold external events into its own canonical ledger and derive current state from that ledger.

### Protocol Richness Is Valuable Only If Control Stays Local

ACP is preferred because it gives structured semantics for session updates, tool calls, and permission requests. Those semantics should improve Manager's governance surface, not replace it.

### Sidecars Are Disposable

Any external runtime process may exit, hang, or lose context. Recovery must assume the sidecar can be recreated at any time. That requires checkpoints that Manager understands without relying on OpenCode private memory.

### Worker Stays Outside The Coding Boundary

The coding path must remain manager-only in both policy and implementation. Worker should not be able to claim, resume, proxy, or host coding work.

### Tool Surface Should Be Backend-Neutral

Existing `codex_session` entrypoints should evolve toward a neutral `coding_session` contract so that OpenCode ACP, OpenCode CLI, Codex CLI, or future ACP-compatible runtimes can sit behind the same manager facade.

## High-Level Architecture

```text
User / API / Tool Surface
        |
        v
Manager Coding Session Facade
        |
        +--> Policy Gate
        |     - manager-only coding ACL
        |     - worker denied
        |
        +--> Session Ledger
        |     - canonical state
        |     - events
        |     - turns
        |     - permissions
        |     - checkpoints
        |     - artifacts/refs
        |
        +--> Runtime Dispatcher
              |
              +--> ACP Adapter
              |     |
              |     +--> Sidecar Supervisor
              |            |
              |            +--> opencode acp --cwd <repo_root>
              |
              +--> CLI Adapter (fallback only)
```

## Concurrency Model

The binding model is intentionally simple in v1:

- one runtime sidecar binding per active workspace;
- one active coding session per workspace at a time;
- one active turn per coding session at a time;
- permission requests are scoped to one turn and one session only.

If a second coding session targets a workspace that already has an active session, Manager should reject it explicitly in v1 rather than multiplexing or implicitly queueing through the same sidecar.

This keeps crash handling, approval isolation, and recovery semantics straightforward in the first iteration. Multi-session-per-workspace support can be considered later only after replay, routing, and failure fan-out semantics are proven.

## Manager Ledger Model

The current single-file `codex_session` snapshot should evolve into a directory-backed `coding_session` ledger.

### Canonical Objects

- `session`: current indexed view of the coding session.
- `turn`: one manager-issued interaction round against a runtime.
- `permission_request`: one runtime request that requires policy or user approval.
- `checkpoint`: Manager-readable recovery material.
- `artifact/ref`: opaque references to logs, patches, commits, PR URLs, outputs, and related files.
- `event`: append-only record of lifecycle changes and runtime observations.

### Recommended File Layout

```text
data/system/coding_sessions/<session_id>/
  session.json
  events.jsonl
  turns/<turn_id>.json
  permissions/<permission_id>.json
  checkpoints/<checkpoint_id>.json
  artifacts/<artifact_id>.json
```

### Session Record Shape

Recommended top-level fields:

```python
{
    "session_id": "cs_...",
    "workspace_id": "ws_...",
    "repo_root": "/abs/path",
    "backend": "opencode",
    "transport": "acp",
    "status": "running",
    "current_turn_id": "turn_...",
    "latest_checkpoint_id": "ckpt_...",
    "runtime_binding_id": "rt_...",
    "summary": "waiting for approval",
    "created_at": "...",
    "updated_at": "...",
}
```

`session.json` is an indexed projection for fast reads. It is not the source of truth by itself.

### Turn Record Shape

Each turn should capture:

- prompt or instruction payload;
- context references injected by Manager;
- policy snapshot used for the turn;
- runtime binding used;
- start and finish timestamps;
- result summary and outcome;
- links to produced artifacts.

This makes the session auditable round by round and gives recovery a natural unit of replay.

### Permission Record Shape

Each permission request should store:

- `permission_id`
- `tool_call_id`
- session and turn linkage
- options presented to policy or user
- chosen decision
- decision source (`policy_auto`, `user`, `system_reject`)
- timestamps

This keeps approval semantics durable even if the sidecar exits before the runtime receives the answer.

### Checkpoint Record Shape

Checkpoints must contain only Manager-readable recovery material, such as:

- current goal summary;
- repository state snapshot reference;
- approved actions so far;
- unresolved questions;
- important refs and artifacts;
- a synthesized `resume_prompt` that can bootstrap a new runtime turn.

Checkpoints must not depend on opaque runtime-private memory.

### Repository Snapshot Semantics

Recovery correctness depends on capturing dirty-worktree state explicitly. Each checkpoint should reference a repository snapshot artifact with at least:

- `workspace_id`
- `repo_root`
- current branch or detached-head marker
- current `HEAD` commit hash if available
- worktree identity if the workspace is a dedicated worktree
- staged diff artifact reference
- unstaged diff artifact reference
- untracked-files manifest reference
- optional ignored-but-relevant file manifest reference if policy includes it
- snapshot timestamp

The first implementation does not need full content-addressed storage, but it must preserve enough information for Manager to understand what changed and to synthesize a recovery prompt grounded in repo reality rather than summary text alone.

For v1 planning, artifact formats should be concretized as:

- text diffs stored as patch artifacts;
- untracked-file manifests stored as structured JSON metadata;
- binary or oversized file changes represented as metadata records with file path, size, hash if available, and classification rather than inline diff content.

Recommended checkpoint cadence:

- after each completed turn;
- immediately before applying a resolved permission that may mutate the repo;
- immediately after any externally visible failure that leaves the worktree dirty.

### Event Log

`events.jsonl` is append-only. Examples:

- `session_created`
- `runtime_bound`
- `turn_started`
- `agent_message_chunk`
- `tool_call_started`
- `tool_call_completed`
- `permission_requested`
- `permission_decided`
- `checkpoint_created`
- `runtime_exited`
- `turn_completed`
- `session_recovered`

Manager rebuilds state by folding the event log and then applying the latest indexed projections.

### Event Correlation And Idempotency

Every ledger event should carry:

- `event_id`: Manager-generated stable ID
- `session_id`
- `turn_id` when applicable
- `runtime_binding_id` when applicable
- `source`: `manager`, `acp`, `cli`, or `recovery`
- `source_event_id` or `source_sequence` if the upstream runtime provides one
- `created_at`

Manager should treat `(source, source_event_id)` as the primary dedupe key when present. If the runtime does not provide a stable external ID, Manager should fall back to deterministic idempotency keys derived from the event kind plus stable correlation fields such as `tool_call_id`, `permission_id`, `turn_id`, and sequence position within the runtime stream.

Replay rules:

- duplicate external events must be ignored after first successful append;
- event folding must be pure and repeatable;
- recovery turns must never reuse the original turn ID;
- permission decisions must be replay-safe and idempotent if resent to a restarted runtime.

## Session State Machine

Recommended canonical states:

- `created`
- `running`
- `waiting_permission`
- `waiting_user`
- `recovering`
- `completed`
- `failed`
- `cancelled`

These states are Manager-owned. ACP notifications may trigger transitions, but only Manager decides and persists the actual state change.

## ACP Runtime Integration

### Sidecar Supervisor Responsibilities

The supervisor is process-focused, not business-focused. Its responsibilities are:

- spawn `opencode acp --cwd <repo_root>`;
- hold stdio handles and connection health metadata;
- map one active runtime binding to one workspace;
- restart or tear down sidecars when instructed;
- report abnormal process exit to Manager.

The supervisor must not decide whether a coding session is recoverable or complete.

In v1, the supervisor also must not multiplex multiple active sessions through one workspace sidecar. It manages one workspace binding that may be leased by only one active session at a time.

### ACP Adapter Responsibilities

The adapter is protocol-focused. Its responsibilities are:

- establish ACP JSON-RPC communication over the sidecar transport;
- translate ACP requests and notifications into Manager events;
- submit Manager decisions back to ACP;
- expose a runtime-neutral interface to the coding-session service.

The adapter must not become the canonical state store.

### Recommended Runtime Interface

```python
ensure_runtime(workspace_id, repo_root) -> runtime_binding
open_session(session_id, runtime_binding)
send_turn(session_id, turn_id, prompt, context_refs, checkpoint_id)
submit_permission_decision(session_id, permission_id, decision)
submit_user_reply(session_id, turn_id, text)
cancel_session(session_id)
handle_runtime_exit(runtime_binding_id, reason)
```

This is intentionally backend-neutral so the same facade can support ACP or CLI adapters.

## ACP Event Mapping

### Streaming Messages

ACP message chunks should be written as `agent_message_chunk` events and optionally streamed to the current user-facing surface. They should not directly replace canonical session state.

### Plans

Plan updates should be stored as optional observability events. They are useful for UI and auditing, but they are not required for recovery correctness.

### Tool Calls

Tool call create and update notifications should map onto turn-scoped events such as:

- `tool_call_started`
- `tool_call_progress`
- `tool_call_completed`
- `tool_call_failed`

Tool call metadata should be retained as artifacts or embedded turn events rather than flattened into the session summary.

### Permission Requests

ACP `session/request_permission` must create a durable permission record and move the session into `waiting_permission` until Manager policy or user choice resolves it.

### Completion

A successful runtime completion does not immediately become canonical truth. Manager should:

1. record the completion event;
2. synthesize a checkpoint if needed;
3. inspect whether follow-up is required;
4. then project the session to `completed`, `waiting_user`, or `failed`.

## Recovery Model

Recovery is Manager-led.

### Sidecar Exit Handling

If a sidecar exits unexpectedly:

1. the supervisor emits `runtime_exited`;
2. Manager appends an event and marks the session `recovering` if recovery is possible;
3. Manager creates or chooses the latest checkpoint;
4. Manager launches a fresh sidecar;
5. Manager creates a new turn with a recovery prompt derived from the checkpoint.

Recovery should not rely on reconnecting to sidecar-private conversation memory.

### Manager Restart Handling

On Manager restart:

- rebuild session views from ledger files;
- detect sessions left in `running`, `waiting_permission`, or `recovering`;
- treat any pre-existing sidecar process as non-authoritative unless a future transport adds explicit replay-safe resume semantics with acknowledged event offsets;
- in v1, terminate or ignore surviving sidecars and launch fresh recovery turns from Manager-owned checkpoints only;
- preserve unresolved approval or user-input requirements.

This is a deliberate hard rule for correctness. Manager restart must never assume that a still-running sidecar contains complete canonical state.

## CLI Fallback

ACP is the primary integration path, but `CLIAdapter` should remain available for:

- environments where ACP cannot be started;
- emergency fallback if ACP is unhealthy;
- phased migration from current coding backends.

Important rule: fallback changes transport, not governance. The same ledger, ACL, and checkpoint model must apply regardless of whether the runtime is ACP or CLI.

CLI fallback is intentionally narrower than ACP in v1:

- it supports only bounded, noninteractive turns;
- permissions must be fully resolved by Manager before the CLI turn starts;
- if the CLI run encounters a condition that would require runtime-driven approval or follow-up questioning, the run must stop and return control to Manager rather than simulating an interactive protocol;
- `waiting_permission` and `waiting_user` remain canonical Manager states, but only ACP is allowed to drive them directly in v1.

## Migration Plan

### Phase 1: Introduce Neutral Naming

- keep `codex_session` as a compatibility-facing facade;
- add internal `coding_session` terminology for new ledger and runtime abstractions;
- stop encoding backend-specific semantics in the public manager contract.

### Phase 2: Add Ledger Infrastructure

- introduce directory-backed ledger storage;
- keep writing compatibility summaries if needed for older call sites;
- start storing turns, permissions, checkpoints, and events separately.

### Phase 3: Add ACP Runtime Path

- implement sidecar supervision and ACP adapter;
- route OpenCode through ACP as the preferred backend;
- leave CLI runtime available as a limited fallback for noninteractive turns.

### Phase 4: Tighten Policy Boundaries

- enforce manager-only coding path at every entrypoint;
- make worker-side coding execution impossible by policy and runtime checks.

## Testing Strategy

### Unit Tests

- ledger fold and projection correctness;
- checkpoint creation and resume-prompt generation;
- permission state transitions;
- ACP event-to-ledger mapping;
- runtime-binding lifecycle bookkeeping.

### Integration Tests

- mocked ACP server path: `start -> tool_call -> permission -> complete`;
- sidecar crash during active turn;
- manager restart with recoverable sessions;
- CLI fallback preserving the same canonical ledger semantics for supported noninteractive turns.

### Policy Regression Tests

- Worker cannot enter coding path;
- coding ACL only allows Manager;
- session recovery never depends on worker execution;
- sidecar-private state loss does not prevent Manager recovery.

## Risks And Guardrails

### Risk: ACP Becomes Accidental Source Of Truth

Guardrail: all ACP notifications are first-class external events, never direct canonical state replacements.

### Risk: Recovery Prompt Drifts From Real Repo State

Guardrail: checkpoints should reference repository snapshots and important refs, not only freeform summaries.

### Risk: Permission Requests Become Stuck After Crashes

Guardrail: approvals are stored as durable permission records with explicit unresolved state and replay-safe decisions.

### Risk: Backend Lock-In Moves From Codex CLI To OpenCode ACP

Guardrail: keep runtime-neutral `coding_session` interfaces and preserve CLI fallback adapters.

## Recommended Initial Scope

The first implementation should stop short of a full runtime ecosystem. It should deliver:

1. neutral `coding_session` ledger foundations;
2. workspace-scoped OpenCode ACP sidecar supervision;
3. single-active-session-per-workspace enforcement;
4. event mapping for messages, tool calls, and permissions;
5. manager-led recovery from sidecar exit;
6. manager-restart recovery from Manager-owned checkpoints only;
7. limited CLI fallback for noninteractive turns only;
8. minimal compatibility shim for existing `codex_session` callers.

Deferred from v1:

- multi-session-per-workspace sidecar multiplexing;
- runtime reattach after Manager restart;
- full parity across every legacy backend;
- broader tool-surface renaming beyond the minimal compatibility shim.

That scope is sufficient to validate the architecture without coupling correctness to a large one-shot migration.
