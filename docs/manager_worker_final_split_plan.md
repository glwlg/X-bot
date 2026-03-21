# Manager/Worker Final Split Plan (One-Shot)

> 历史方案文档：基于已删除的独立 Worker 架构，仅保留作演进记录，不代表当前仓库实现。

## Goal

Implement a full control-plane/data-plane split with no legacy compatibility path:

- Manager owns orchestration, governance, failure diagnosis, and code evolution.
- Worker owns task execution only.
- Shared layer is the single source for contracts, queue, policy, and runtime adapters.
- Worker is dynamically updatable by Manager through versioned Worker Program artifacts.

No fallback routing, no dual runtime, no compatibility branches.

## Hard Constraints

- No direct `manager <-> worker` imports.
- Dependency direction is strict: `manager -> shared`, `worker -> shared`.
- Dynamic code edits can target Worker Program only, not Worker Kernel.
- `coding_backend` is Manager-only.
- Queue/state protocol is file-backed and deterministic.

## Target Runtime Topology

- `x-bot-manager`: receives user input, plans, dispatches, governs.
- `x-bot-worker`: pulls tasks from shared queue, executes Worker Program, writes results/events.

## Target Code Layout

```text
src/
├── manager/
│   ├── dispatch/
│   │   └── service.py
│   ├── governance/
│   │   └── registry_service.py
│   ├── evolution/
│   │   └── repair_service.py
│   └── relay/
│       └── result_relay.py
├── worker/
│   ├── kernel/
│   │   ├── daemon.py
│   │   └── program_loader.py
│   └── program_api.py
├── shared/
│   ├── contracts/
│   │   ├── dispatch.py
│   │   └── programs.py
│   ├── queue/
│   │   ├── jsonl_queue.py
│   │   └── dispatch_queue.py
│   ├── policy/
│   └── runtime/
└── worker_main.py
```

## Data Model (Final)

All task/control/event data is stored under `data/system/dispatch/`:

- `tasks.jsonl`: task envelopes and status transitions.
- `results.jsonl`: final task results.
- `control_commands.jsonl`: manager->worker control commands.
- `control_events.jsonl`: worker->manager control events.

Worker Program artifacts are stored under `data/system/worker_programs/`:

- `<program_id>/<version>/manifest.json`
- `<program_id>/<version>/program.py`

Worker binding:

- `data/system/workers/active_bindings.json` maps worker to active program version.

## Responsibilities

### Manager

- Worker selection and task dispatch.
- Failure classification and retry budgeting.
- Dynamic Worker Program generation/patching via `coding` skill + `coding_backend`.
- Program release, activation, and rollback.
- Result relay to user channels.

### Worker Kernel

- Claim task -> load active Worker Program -> execute -> publish result.
- Never perform governance decisions.
- Never edit Manager code.

### Shared

- Contracts, queue operations, lock semantics, serialization.
- Cross-process policy primitives.
- Common runtime adapters reused by Manager and Worker.

## One-Shot Refactor Scope

Remove and replace worker runtime paths in one cut:

- Remove `src/core/worker_runtime.py`.
- Remove `src/worker_runtime/daemon.py`.
- Remove `src/worker_runtime/task_file_store.py`.
- Remove `src/worker_runtime/result_relay.py`.
- Replace call sites to use `manager.*` and `worker.*` packages.
- Introduce `src/worker_main.py` as the only worker process entrypoint.

## Dynamic Modification Pipeline (Final)

1. Worker task fails with structured result.
2. Manager classifies error.
3. If code-fixable, Manager invokes `coding` skill targeting `data/system/worker_programs/<program_id>/draft/`.
4. Manager validates draft (contract + tests + static checks).
5. Manager promotes draft to immutable `<version>` artifact and writes manifest/checksum.
6. Manager sends activation command.
7. Worker switches active program and reports control event.
8. On health failure, Manager rolls back to previous version.

## Safety Rules

- Worker Program write scope: `data/system/worker_programs/**` only.
- Kernel path is read-only to `coding_backend`.
- Activation requires manifest checksum validation.
- Every activation emits audit event with actor, program_id, version, reason.

## Validation Gates

- Layer dependency test forbids manager-worker cross import.
- Contract round-trip serialization tests.
- Queue claim/finish concurrency tests.
- End-to-end: dispatch -> worker execute -> result relay.
- End-to-end: failed version activation -> rollback.

## Completion Criteria

- No runtime path imports `core.worker_runtime` or `worker_runtime.*`.
- Manager dispatch and relay run entirely through `manager.* + shared.*`.
- Worker daemon runs entirely through `worker.* + shared.*`.
- Worker Program update/activate/rollback loop is functional.
- All updated tests pass.
