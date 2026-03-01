# PROJECT KNOWLEDGE BASE

**Refreshed:** 2026-03-01 Asia/Shanghai
**Commit (pre-refresh):** 163cdf9
**Branch:** develop

## OVERVIEW
X-Bot is a Python multi-platform AI bot with a Core Manager + Worker Fleet architecture.
Runtime state is file-backed under `data/`: Core Manager handles orchestration/governance, while Worker Kernel executes queued tasks and reports results via relay.

## STRUCTURE
```text
./
|- src/                  # production code
|  |- core/              # orchestration, policy, tool/runtime composition, state access
|  |- handlers/          # chat/command entrypoints
|  |- manager/           # manager dispatch/dev pipeline + result relay
|  |- worker/            # worker kernel + program runtime
|  |- platforms/         # Telegram/Discord/DingTalk/Web adapters
|  |- services/          # AI/download/stock/sandbox/web integrations
|  `- shared/            # cross-runtime contracts and dispatch queue
|- skills/               # builtin + learned skills
|- tests/                # core/integration/manager async pytest suites
|- data/                 # runtime state and persisted user/system data
|- DEVELOPMENT.md        # authoritative architecture constraints
|- pyproject.toml        # project metadata + deps
|- pytest.ini            # test discovery + async mode
`- docker-compose.yml    # local deployment and service topology
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| App bootstrap and platform wiring | `src/main.py` | `async def main()` starts adapters, schedulers, heartbeat, and relay |
| Worker kernel runtime | `src/worker_main.py`, `src/worker/kernel/daemon.py` | queue polling, task execution, cancellation, result write-back |
| Manager dispatch and async delivery | `src/manager/dispatch/service.py`, `src/manager/relay/result_relay.py` | worker selection/queue submit and platform result fan-out |
| Orchestration/tool routing | `src/core/agent_orchestrator.py`, `src/core/orchestrator_runtime_tools.py`, `src/core/tool_registry.py` | unified tool surface and call path |
| Extension execution pipeline | `src/core/extension_router.py`, `src/core/extension_executor.py`, `src/core/tools/extension_tools.py` | `ExtensionTools.run_extension` is the skill gateway |
| Prompt/system policy injection | `src/core/prompt_composer.py`, `src/core/prompts.py`, `src/core/soul_store.py` | personality + policy chain |
| Persistence behavior | `src/core/state_store.py`, `src/core/state_paths.py`, `src/core/state_io.py`, `src/core/state_file.py` | canonical file-backed state protocol |
| Message handling | `src/handlers/ai_handlers.py`, `src/handlers/worker_handlers.py`, `src/handlers/heartbeat_handlers.py` | primary user request and worker control paths |
| Queue contracts | `src/shared/contracts/dispatch.py`, `src/shared/queue/dispatch_queue.py` | task envelope/result lifecycle |
| High-signal orchestration tests | `tests/core/test_orchestrator_single_loop.py`, `tests/core/test_orchestrator_delivery_closure.py`, `tests/core/test_worker_result_relay.py` | manager loop and async delivery closure |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `main` | async function | `src/main.py` | app boot + adapter registration |
| `run_worker_kernel` | async function | `src/worker/kernel/daemon.py` | worker daemon runtime loop |
| `manager_dispatch_service` | module singleton | `src/manager/dispatch/service.py` | worker selection and task enqueue |
| `worker_result_relay` | module singleton | `src/manager/relay/result_relay.py` | async result delivery to platforms |
| `ExtensionTools.run_extension` | async method | `src/core/tools/extension_tools.py` | skill execution gateway |
| `user_path` | function | `src/core/state_paths.py` | canonical per-user storage pathing |

## CONVENTIONS
- Runtime is file-system first; state access is centralized via `core.state_store` and `core.state_paths/state_io`.
- `DEVELOPMENT.md` defines boundaries: Core Manager orchestrates/governs; Worker Kernel executes user-facing jobs.
- Async-first test style: `pytest` with `asyncio_mode=auto`; most core/manager tests are `@pytest.mark.asyncio`.
- Project packaging uses `pyproject.toml` + `hatchling`; no `package.json` or JS build pipeline.

## ANTI-PATTERNS (THIS PROJECT)
- Do not route regular user execution into Core Manager business logic (`DEVELOPMENT.md`).
- Do not treat `/worker` as the only task entrypoint; normal chat should auto-dispatch when needed.
- Do not bypass `state_store`/`state_paths` with ad-hoc file paths.
- Do not skip dispatch queue contracts when adding manager-worker integrations.
- Do not upgrade to heavy coding tools when read/write/bash/browser primitives already solve the task (`DEVELOPMENT.md`).

## UNIQUE STYLES
- Mixed-language docs are normal (English + Chinese) in architecture and feature docs.
- Platform support is parallel by design (Telegram/Discord/DingTalk/Web), not plugin-afterthought.
- Skills are first-class runtime extensions with per-skill contracts in `SKILL.md`.

## COMMANDS
```bash
uv sync
uv run python src/main.py
uv run python src/worker_main.py
uv run pytest
docker compose up --build -d
docker compose logs -f x-bot
```

## NOTES
- `docker-compose.yml` uses `network_mode: host` and mounts host Docker socket.
- `X_DEPLOYMENT_STAGING_PATH` must be absolute and mapped host==container path.
- LSP diagnostics may be unavailable unless `basedpyright` is installed.
