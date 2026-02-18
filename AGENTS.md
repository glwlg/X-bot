# PROJECT KNOWLEDGE BASE

**Generated:** 2026-02-17 23:59 Asia/Shanghai
**Commit:** 66b12d6
**Branch:** develop

## OVERVIEW
X-Bot is a Python multi-platform AI bot with a Core Manager + Worker Fleet architecture. Runtime state is file-backed under `data/`, while user features are split across handlers, repositories, skills, and platform adapters.

## STRUCTURE
```text
./
|- src/                  # production code
|  |- core/              # orchestration, routing, memory, tool registry
|  |- handlers/          # chat/command entrypoints
|  |- platforms/         # Telegram/Discord/DingTalk adapters
|  |- repositories/      # file-backed persistence layer
|  `- worker_runtime/    # worker daemon + relay + file locking
|- skills/               # builtin + learned skills
|- tests/                # core-heavy async pytest suite
|- data/                 # runtime state and persisted user/system data
|- DEVELOPMENT.md        # authoritative architecture constraints
|- pyproject.toml        # project metadata + deps
|- pytest.ini            # test discovery + async mode
`- docker-compose.yml    # local deployment and service topology
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| App bootstrap and platform wiring | `src/main.py` | `async def main()` is runtime entrypoint |
| Orchestration/tool routing | `src/core/agent_orchestrator.py`, `src/core/orchestrator_runtime_tools.py`, `src/core/tool_registry.py` | `run_extension` path is central |
| Prompt/system policy injection | `src/core/prompt_composer.py`, `src/core/prompts.py` | Core personality + policy chain |
| Persistence behavior | `src/repositories/base.py` + `src/repositories/*_repo.py` | `user_path(...)` defines data layout |
| Message handling | `src/handlers/ai_handlers.py`, `src/handlers/worker_handlers.py` | Main user request path |
| Platform-specific behavior | `src/platforms/{telegram,discord,dingtalk}` | Adapter + formatter + mapper split |
| Worker runtime internals | `src/core/worker_runtime.py`, `src/worker_runtime/*` | Daemon and file-backed task flow |
| Tests for orchestration | `tests/core/test_orchestrator_single_loop.py`, `tests/core/test_orchestrator_delivery_closure.py` | Highest-signal behavior tests |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `main` | async function | `src/main.py` | app boot + adapter registration |
| `run_extension` | async function | `src/core/tools/extension_tools.py` | skill execution gateway |
| `ToolRegistry` | class | `src/core/tool_registry.py` | declares callable tool surface |
| `worker_runtime` | module object | `src/core/worker_runtime.py` | core execution runtime |
| `user_path` | function | `src/repositories/base.py` | canonical per-user storage pathing |

## CONVENTIONS
- Runtime is file-system first; repository layer is expected to persist under `data/users` and `data/system/repositories`.
- `DEVELOPMENT.md` sets architecture constraints: Core Manager orchestrates; Worker Fleet executes user tasks.
- Async-first test style: `pytest` with `asyncio_mode=auto`; most core tests are `@pytest.mark.asyncio`.
- Project-level packaging uses `pyproject.toml` + `hatchling`; no `package.json` or JS build pipeline.

## ANTI-PATTERNS (THIS PROJECT)
- Do not route regular user execution into Core Manager business logic (`DEVELOPMENT.md`).
- Do not treat `/worker` as the only task entrypoint; normal chat must auto-dispatch (`DEVELOPMENT.md`).
- Do not bypass repository base primitives with ad-hoc storage paths (`DEVELOPMENT.md`, `src/repositories/base.py`).
- Do not upgrade to heavy coding tools when read/write/bash/browser primitives already solve the task (`DEVELOPMENT.md`).

## UNIQUE STYLES
- Mixed-language docs are normal (English + Chinese) in architecture and feature docs.
- Platform support is parallel by design (Telegram/Discord/DingTalk), not plugin-afterthought.
- Skills are first-class runtime extensions with per-skill contracts in `SKILL.md`.

## COMMANDS
```bash
uv sync
uv run python src/main.py
uv run pytest
docker compose up --build -d
docker compose logs -f x-bot
```

## NOTES
- `docker-compose.yml` uses `network_mode: host` and mounts host Docker socket.
- `X_DEPLOYMENT_STAGING_PATH` must be absolute and mapped host==container path.
- LSP diagnostics may be unavailable unless `basedpyright` is installed.
