# Delivery Reliability And State Boundaries Design

Date: 2026-03-12
Status: Draft reviewed with user in chat
Scope: Manager, API, scheduler, heartbeat, delivery, state access

## Background

Recent delivery work improved worker result relay durability, but proactive pushes still use a weaker path. RSS, reminders, cron pushes, and some heartbeat notifications can still fail silently or depend on heuristic delivery-target resolution. At the same time, state ownership has drifted away from the documented `data/users/<user_id>/...` model into a shared private root that collapses multiple users into a canonical `"user"` bucket.

The result is a system where:

- worker result delivery is relatively durable;
- proactive push delivery is materially weaker;
- user state ownership is ambiguous;
- web/API mutations do not always synchronize runtime behavior;
- delivery metadata is spread across multiple dict-heavy boundaries.

This design unifies those areas into one reliability-focused change program.

## Goals

1. Make proactive delivery as reliable and observable as worker result delivery.
2. Restore real per-user state ownership while preserving explicit shared/global state where needed.
3. Remove heuristic cross-user or cross-task delivery-target guessing.
4. Make web/API state changes reflect actual runtime behavior for scheduler and heartbeat surfaces.
5. Reduce future regressions by introducing stronger delivery and metadata boundaries.

## Non-Goals

- Replacing the file-system-first architecture.
- Moving state to a database.
- Rewriting all platform adapters.
- Redesigning the entire worker dispatch model.
- Introducing new product domains unrelated to delivery, scheduling, heartbeat, or state boundaries.

## Design Principles

### One Owner Per Concern

- `state_paths` and `state_store` remain the canonical user/system persistence layer.
- delivery execution and delivery status must have one canonical ledger.
- API endpoints should go through application services for mutating scheduler/heartbeat state, not raw one-off writes plus implicit runtime assumptions.

### No Delivery Guessing

The system must not infer a push target from recent unrelated tasks. A background notification may only be delivered if the target comes from explicit task metadata or explicit user-scoped delivery bindings/state.

### Confirmation Before Completion

Producer flows such as reminders or RSS may only finalize user-visible state after delivery is confirmed or explicitly suppressed. Logging a send attempt is not success.

### Compatibility First, Then Tightening

Migration from shared-user paths to real per-user paths must be staged. Reads may remain backward-compatible during migration, but new writes should converge on the new canonical layout.

## Workstreams

## Workstream A: User-Scoped State Ownership

### Problem

`src/core/state_paths.py` currently routes `user_path()` into a shared private root and `all_user_ids()` effectively returns only `"user"` when any content exists. This breaks per-user isolation for scheduler, RSS, watchlists, reminders, and heartbeat-adjacent state.

### Design

Introduce a strict separation between:

- real per-user state: `data/users/<safe_user_id>/...`
- explicit shared state: one clearly named shared root for cases that are intentionally global or platform-shared
- system state: existing `data/system/...`

`user_path(user_id, *parts)` must resolve to a true user-owned subtree. Shared/global reads should use a separate explicit helper instead of smuggling everything through `user_path()`.

### Required Changes

- Refactor `src/core/state_paths.py` so `user_path()` uses the supplied user id.
- Add explicit helpers for shared-user or global paths where still needed.
- Update `src/core/state_store.py` call sites that depend on the current implicit shared pathing.
- Ensure enumerators such as `all_user_ids()` return actual discovered user ids.

### Migration Strategy

Stage 1:

- keep compatibility reads from legacy shared/private roots;
- route all new writes to canonical per-user paths;
- add targeted migration helpers for scheduler/RSS/reminder/watchlist data.

Stage 2:

- migrate legacy user-owned content from shared root into discovered user roots;
- log or surface ambiguous records that cannot be confidently attributed.

Stage 3:

- remove broad fallback reads once migrated state is stable.

### Migration Attribution Rules

Migration must be domain-specific, idempotent, and reportable.

| Domain | Legacy source | Ownership signal | Canonical destination | Ambiguous-case behavior |
|---|---|---|---|---|
| Scheduled tasks | shared/private automation scheduler files | task `user_id`, platform binding, or explicit legacy metadata | `data/users/<uid>/automation/...` | leave in quarantine report; do not auto-assign |
| RSS subscriptions/state | shared/private RSS subscription files | subscription `user_id`, explicit platform uid, or binding lookup | `data/users/<uid>/rss/...` | leave unread and report |
| Reminders | shared/private reminders files | reminder `user_id`, `chat_id` to binding lookup, or explicit metadata | `data/users/<uid>/automation/reminders...` | keep source record, report ambiguity |
| Watchlist | shared/private watchlist files | explicit platform uid or binding lookup | `data/users/<uid>/stocks/...` | keep source record, report ambiguity |
| Heartbeat state | shared/private heartbeat files | explicit heartbeat owner id | `data/users/<uid>/heartbeat/...` | do not auto-split shared records without owner |

Migration requirements:

- migration is idempotent and safe to re-run;
- every run writes a migration ledger/report with migrated, skipped, and ambiguous counts;
- fallback reads are only removable after the report shows zero unreviewed ambiguous records for the targeted domains.

## Workstream B: Unified Proactive Delivery Reliability

### Problem

`src/core/scheduler.py` and `src/core/background_delivery.py` still treat proactive pushes as best-effort side effects. Delivery failure often becomes only a warning log. RSS updates can be marked read after attempted delivery, and reminders can be deleted even when delivery fails.

### Design

All proactive push producers will use the same delivery lifecycle semantics as worker result delivery:

- create or ensure a delivery job;
- resolve an explicit target;
- deliver through a shared executor;
- update canonical delivery status;
- only then finalize producer-specific state.

Producer types in scope:

- RSS updates
- reminders
- cron skill reports
- heartbeat proactive notifications

### Delivery States

Canonical states remain:

- `pending`
- `retrying`
- `delivered`
- `dead_letter`
- `suppressed`

### Canonical Delivery Job Model

Both relay-driven and proactive-delivery producers use one canonical delivery job model backed by the existing delivery ledger.

Required fields:

- `job_id`: stable canonical job id; for relay this may equal `task_id`, for proactive producers it should use a namespaced shape such as `proactive:<producer>:<producer_record_id>:<attempt_scope>`
- `producer_type`: `relay | rss | reminder | cron | heartbeat`
- `producer_id`: producer-owned stable identifier
- `idempotency_key`: `producer_type + producer_id + target + normalized_payload_hash`
- `target_platform`
- `target_chat_id`
- `status`
- `attempts`
- `next_retry_at`
- `last_error`
- `payload summary/body mode`

Ownership rules:

- `delivery_store` is the canonical delivery ledger for both relay and proactive producers;
- one delivery executor owns retry/backoff and status transitions;
- producer code may create/ensure jobs and consume structured terminal/intermediate states, but may not invent its own retry semantics;
- repeated producer emissions with the same idempotency key must dedupe to the same outstanding job unless prior delivery reached a terminal state and policy explicitly allows re-emission.

### Required Changes

- Create a proactive-delivery application layer that wraps `delivery_store` and background adapter execution.
- Refactor `send_via_adapter()` callers so they consume a structured delivery result, not a boolean-or-log side effect.
- Update reminder and RSS finalization logic so state mutation happens only after confirmed delivery or explicit suppression policy.
- Reuse retry/backoff/dead-letter semantics already established in manager relay.

### Expected Producer Behavior

- Reminder: do not delete reminder record until delivery is confirmed or intentionally suppressed.
- RSS: do not mark updates read until delivery is confirmed.
- Cron: do not silently skip if no target exists; record retry/dead-letter state.
- Heartbeat proactive notifications: same delivery semantics, same observability surface.

## Workstream C: Explicit Delivery Target Resolution

### Problem

`_resolve_proactive_delivery_target()` currently includes heuristic fallback by scanning recent dispatch tasks for the same platform. That can misroute notifications and is incompatible with strict delivery correctness.

### Design

Delivery target resolution must allow only:

1. explicit delivery target carried by task metadata; or
2. explicit user-scoped target stored in heartbeat/delivery binding state; or
3. an explicit platform binding lookup owned by the user being served.

If none of those exist, the job must not guess. It should move into retry/dead-letter flow with a clear reason such as `missing_delivery_target`.

### Target Resolution Precedence

Canonical precedence is:

1. explicit target embedded in task/job metadata;
2. explicit resource-level binding for the owned feature record;
3. explicit user default binding for the requested platform.

Rules:

- one proactive delivery job resolves to one target, not multi-target fan-out;
- fan-out is a separate future feature and out of scope here;
- aliases such as `worker_runtime -> telegram` normalize before binding resolution;
- missing or mismatched platform normalization must not silently downgrade into another platform or another user's target.

### Canonical Binding Contract

Binding lookup must operate on a stable contract with at least:

- `owner_user_id`
- `platform`
- `platform_user_id`
- `is_default`
- optional `resource_scope` for feature-specific bindings

Resource-level binding outranks user default binding only when the resource owner matches the job owner.

### Required Changes

- Remove `_recent_delivery_target_for_platform()` from proactive delivery decision making.
- Narrow `_resolve_proactive_delivery_target()` to explicit, user-owned sources only.
- Add tests proving cross-user fallback cannot occur.
- Ensure worker-runtime aliases like `worker_runtime -> telegram` are normalized without reintroducing heuristic chat-id inference.

## Workstream D: Web/API And Runtime Consistency

### Problem

Scheduler and heartbeat APIs currently mutate file-backed state without reliably synchronizing APScheduler/runtime behavior. Some endpoints also assume Telegram-only binding even though runtime adapters are multi-platform.

### Design

Introduce application services for scheduler and heartbeat mutations. Endpoints call services that:

1. resolve the target binding/platform;
2. mutate canonical state;
3. synchronize runtime state incrementally or trigger a controlled reload;
4. return actual effective status.

### Mutation Semantics

Scheduler and heartbeat mutation endpoints must define explicit write ordering and failure behavior.

Required semantics:

- canonical state write happens first;
- runtime sync is attempted inline for the same request where feasible;
- if runtime sync fails after state write, the endpoint returns failure with a structured `runtime_sync_failed` result and leaves a dirty-state marker for reconciliation;
- restart reconciliation must inspect dirty-state markers and re-attempt runtime sync;
- if an endpoint intentionally uses deferred sync, the response must say `accepted` rather than `success`, and the sync status must be queryable.

Default policy for this project:

- scheduler CRUD/status updates: inline sync preferred, full reload fallback if incremental sync fails;
- heartbeat config mutations: inline state update plus in-process runtime refresh when needed;
- no endpoint should report plain success when canonical state and runtime state are known to be divergent.

### Scheduler API Behavior

- list all tasks, not only active tasks, and include `is_active` explicitly;
- create/update/delete/status changes trigger runtime sync;
- runtime sync prefers incremental scheduler job updates where possible, with full reload as fallback;
- API no longer presents disabled tasks as if they do not exist.

### Heartbeat API Behavior

Expose both checklist and runtime control/status:

- checklist CRUD
- pause/resume
- run now
- update cadence
- update active hours
- last level
- last run time
- last error
- next due if available

### Platform Binding Behavior

Web/API surfaces should not silently assume Telegram. Platform must become an explicit request or resource dimension for RSS, scheduler, watchlist, and monitor features.

## Workstream E: Metadata Contract Tightening

### Problem

Critical manager/worker and relay boundaries still depend on loosely typed `metadata` dict keys such as `platform`, `chat_id`, `user_id`, `session_task_id`, `task_inbox_id`, and `stage_plan`. This makes boundary drift easy and regressions silent.

### Design

Introduce versioned typed metadata models for the most critical boundaries:

- delivery target metadata
- dispatch session metadata
- closure/session metadata

These models should be serialized into task metadata under stable namespaces and validated at submit, relay, and closure boundaries.

### Required Changes

- define typed structures in `src/shared/contracts/` or an adjacent shared contract module;
- update dispatch submission in `src/manager/dispatch/service.py` and runtime extraction in `src/core/skill_tool_handlers.py`;
- update relay and closure consumers to validate/access typed payloads instead of raw scattered keys.

### Minimum Phase-2 Contract

Because heuristic targeting is removed earlier than the full metadata cleanup, Phase 2 must introduce a minimal validated delivery-target contract before the larger Phase 4 refactor completes.

Accepted temporary legacy keys during Phase 2:

- `platform`
- `chat_id`
- `user_id`
- `task_inbox_id`
- `session_task_id`

Phase-2 compatibility layer requirements:

- normalize these legacy keys into the new minimal delivery-target model at boundary entry;
- reject incomplete or cross-user inconsistent combinations explicitly;
- record which code paths still emit legacy metadata;
- define a hard removal point during Phase 4 once all producers are migrated.

## Workstream F: Relay Hot-Path Simplification

### Problem

`WorkerResultRelay` currently mixes progress draining, target resolution, content preparation, delivery retry, session sync, and closure-adjacent side effects. This is the most reliability-sensitive path in the system and should be narrower.

### Design

Split relay into clearer phases:

1. determine eligibility and target;
2. deliver canonical result and files;
3. persist delivery status;
4. emit post-delivery side effects such as summarization, closure sync, or learned-skill repair triggers.

This keeps delivery correctness separate from higher-level enrichment or workflow progression.

### Refactor Boundary

- keep user-visible delivery on the hot path;
- move enrichment/secondary effects into post-processors or explicit follow-up steps;
- preserve existing closure behavior, but stop making plain result delivery depend on optional enrichment.

### Relay Side-Effect Matrix

| Behavior | Category | Allowed to affect delivery outcome? |
|---|---|---|
| resolve canonical target | blocking for delivery completion | yes |
| deliver plain text/files | blocking for delivery completion | yes |
| persist delivery ledger status | blocking for delivery completion | yes |
| ack delivered progress events | blocking only for progress bookkeeping, not final content correctness | no final-delivery downgrade |
| session/task inbox sync | post-delivery required follow-up | must not mark user delivery failed |
| summarization | post-delivery best-effort | no |
| learned-skill auto-repair trigger | post-delivery best-effort | no |
| closure enrichment / waiting-user card follow-up | post-delivery required follow-up | must not change successful delivery into failed |

Persistence ordering:

1. canonical payload prepared
2. user-visible delivery attempted
3. delivery ledger updated
4. queue/session sync updated
5. enrichment and workflow side effects executed

Once step 3 records `delivered`, later failures may create follow-up warnings/tasks but must not rewrite delivery outcome to failed.

## Subagent Execution Strategy

This implementation is too broad for uncontrolled parallel edits. Work is split into serial core tracks and parallel-safe edge tracks.

### Controller-Owned Serial Tracks

- Workstream A: user-scoped state ownership
- Workstream B: unified proactive delivery reliability
- Workstream C: explicit delivery target resolution
- Workstream E: metadata contract tightening
- Workstream F: relay hot-path simplification

These tracks share state, delivery semantics, and contract boundaries and must be coordinated centrally.

### Parallel-Safe Subagent Tracks

- Workstream D scheduler/API consistency
- Heartbeat web control and observability
- `/remind` command entrypoint and help-text correction
- targeted regression/integration test expansion that does not change the same production files as the serial core tracks

### Guardrails

- no two implementers may edit `src/core/state_paths.py`, `src/core/state_store.py`, `src/core/scheduler.py`, or `src/manager/relay/result_relay.py` at the same time;
- edge-track agents must not invent new state ownership rules;
- serial core changes land behind tests first.

## Error Handling And Observability

### Structured Failure Reasons

Delivery failures should distinguish at least:

- `missing_delivery_target`
- `adapter_send_failed`
- `delivery_state_persist_failed`
- `runtime_sync_failed`
- `suppressed_by_policy`

Producer and API layers should consume structured results rather than infer failure from logs.

### Health Surfaces

Expand the health surface beyond basic liveness:

- delivery health summary
- retrying/dead-letter counts
- oldest undelivered age
- recent delivery errors
- scheduler sync status
- heartbeat runtime status summary

The existing simple health endpoint may remain for liveness, but a richer operational endpoint should be added for reliability monitoring.

## Backward Compatibility Policy

### Existing API Requests

During rollout, existing Telegram-shaped web/API requests remain accepted for compatibility, but their behavior must be explicit.

- if `platform` is omitted, current default behavior may resolve to Telegram only during the compatibility window;
- responses should include the resolved platform so callers can see what happened;
- compatibility mode should emit a warning or response flag indicating platform defaulting was used;
- once platform-explicit clients are deployed, API surfaces for RSS, scheduler, watchlist, and monitor move to mandatory platform input in the final cleanup phase.

### Existing State And Metadata

- legacy shared-root state remains readable during migration only;
- legacy metadata keys remain accepted only through the Phase-2 compatibility layer;
- removal of either compatibility path requires passing migration and regression checks defined in this spec.

## Testing Strategy

### Test-First Requirements

Every behavior change in this program follows TDD. High-signal regression tests are required before production changes.

### Required Test Groups

1. state scope isolation
   - different users do not share scheduler/RSS/watchlist/reminder data unless explicitly configured as shared

2. proactive delivery target correctness
   - no cross-user fallback
   - no platform-wide recent-task guessing
   - worker-runtime alias normalization remains correct

3. proactive delivery lifecycle
   - reminder not deleted before confirmed delivery
   - RSS not marked read before confirmed delivery
   - missing target enters retry/dead-letter path

4. web/runtime synchronization
   - scheduler API create/update/delete/status changes are reflected in runtime behavior
   - heartbeat web controls reflect effective state

5. metadata contract validation
   - invalid or incomplete delivery/session metadata fails explicitly instead of degrading into implicit runtime behavior

6. relay hot-path regression coverage
   - canonical result delivery still works while post-delivery side effects are separated

### Verification Commands

At minimum, the implementation plan should preserve or expand validation around:

- `tests/core/test_scheduler_rss_links.py`
- `tests/core/test_background_delivery.py`
- `tests/core/test_telegram_adapter_send_message.py`
- `tests/core/test_worker_result_relay.py`
- `tests/core/test_orchestrator_delivery_closure.py`
- relevant API endpoint tests for scheduler, monitor, RSS, and watchlist

## Rollout Plan

### Phase 1

- restore user-scoped paths with compatibility reads;
- add tests protecting user isolation.

### Phase 2

- unify proactive delivery around canonical delivery states;
- introduce the minimum validated delivery-target metadata contract;
- remove heuristic delivery-target fallback.

### Phase 3

- make API mutations synchronize runtime scheduler/heartbeat behavior;
- improve web control and observability.

### Phase 4

- tighten metadata contracts and simplify relay hot path.

Although implementation may happen in one development stream, these phases define the safe order of compatibility cuts.

## Risks And Mitigations

- Risk: legacy shared-state users lose visibility into existing data.
  - Mitigation: compatibility reads plus explicit migration helpers and tests.

- Risk: removing heuristic target fallback causes more jobs to wait in retry/dead-letter.
  - Mitigation: this is preferable to wrong-recipient delivery; add observability and rebind flows.

- Risk: scheduler runtime sync introduces duplicate or stale jobs.
  - Mitigation: incremental sync with idempotent job ids and fallback full reload tests.

- Risk: typed metadata refactor breaks existing worker submissions.
  - Mitigation: versioned contracts, compatibility deserializers, and staged rollout.

## Success Criteria

- regression tests prove no cross-user proactive delivery and no recent-task fallback targeting;
- reminder and RSS state never finalize before delivery reaches `delivered` or `suppressed`;
- missing-target proactive jobs surface as `retrying` or `dead_letter` with structured reason;
- state-path tests prove different users do not share scheduler/RSS/watchlist/reminder data unless explicitly marked shared;
- scheduler API mutations are reflected in runtime state within the same sync attempt or return structured `runtime_sync_failed` status;
- migration reports show zero unreviewed ambiguous records for domains whose fallback reads are removed;
- metadata normalization tests prove invalid boundary payloads fail explicitly rather than silently degrading into guessed behavior;
- relay tests prove post-delivery enrichment failures do not convert a successful user-visible delivery into a failed one.
