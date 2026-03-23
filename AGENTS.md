# PROJECT KNOWLEDGE BASE

**Refreshed:** 2026-03-22 Asia/Shanghai
**Commit (pre-refresh):** 4edda2b
**Branch:** develop

## OVERVIEW
X-Bot is a Python multi-platform AI bot with a Core Manager + API Service architecture.
Runtime state lives under `data/`: most state remains file-backed, while bounded aggregate/query-heavy data uses SQLite in `data/bot_data.db`. Core Manager handles orchestration, delivery, task/session governance, heartbeat, model control, LLM usage accounting, and developer tooling. When needed, it may start in-process subagents, but there is no separate Worker runtime anymore.

## STRUCTURE
```text
./
|- src/                  # production code
|  |- api/               # FastAPI + SPA
|  |- core/              # orchestration, policy, tool/runtime composition, state access
|  |- handlers/          # chat/command entrypoints
|  |- manager/           # manager-side planning/dev/closure services
|  |- platforms/         # Telegram/Discord/DingTalk/Web adapters
|  |- services/          # AI/download/stock/sandbox/web integrations
|  `- shared/            # cross-module contracts and shared types
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
| App bootstrap and platform wiring | `src/main.py` | `async def main()` starts adapters, heartbeat, and API integration |
| API bootstrap | `src/api/main.py` | FastAPI app and SPA hosting |
| Orchestration/tool routing | `src/core/agent_orchestrator.py`, `src/core/orchestrator_runtime_tools.py`, `src/core/tool_registry.py` | unified tool surface and call path |
| Extension execution pipeline | `src/core/extension_router.py`, `src/core/extension_executor.py`, `src/core/tools/extension_tools.py` | `ExtensionTools.run_extension` is the skill gateway |
| Prompt/system policy injection | `src/core/prompt_composer.py`, `src/core/prompts.py`, `src/core/soul_store.py` | personality + policy chain |
| Persistence behavior | `src/core/state_store.py`, `src/core/state_paths.py`, `src/core/state_io.py`, `src/core/state_file.py` | canonical file-backed state protocol |
| Model config and runtime switching | `src/core/model_config.py`, `src/handlers/model_handlers.py` | `config/models.json` source of truth and `/model` control plane |
| LLM usage accounting | `src/core/llm_usage_store.py`, `src/handlers/usage_handlers.py`, `src/user_context.py` | per-day/per-session/per-model aggregation, token estimation, `/usage` entrypoint |
| Message handling | `src/handlers/ai_handlers.py`, `src/handlers/message_utils.py`, `src/handlers/heartbeat_handlers.py` | primary user request, multimodal preprocessing, and heartbeat entrypoints |
| Vision input preprocessing | `src/services/image_input_service.py`, `src/handlers/message_utils.py` | URL/local image resolution into `inline_data` |
| High-signal orchestration tests | `tests/core/test_orchestrator_single_loop.py`, `tests/core/test_orchestrator_delivery_closure.py`, `tests/core/test_task_inbox.py` | manager loop, closure, and task state |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `main` | async function | `src/main.py` | app boot + adapter registration |
| `ExtensionTools.run_extension` | async method | `src/core/tools/extension_tools.py` | skill execution gateway |
| `heartbeat_worker` | module singleton | `src/core/heartbeat_worker.py` | background heartbeat scheduler |
| `llm_usage_store` | module singleton | `src/core/llm_usage_store.py` | OpenAI-compatible call accounting into `data/bot_data.db` |
| `user_path` | function | `src/core/state_paths.py` | canonical per-user storage pathing |

## CONVENTIONS
- Runtime is file-system first; state access is centralized via `core.state_store` and `core.state_paths/state_io`, while aggregate/query-heavy data may use bounded SQLite tables in `data/bot_data.db`.
- `DEVELOPMENT.md` defines boundaries: Core Manager is the only user-facing runtime; image URL/local path inputs are normalized before the LLM call.
- Model/provider selection is owned by `config/models.json`; runtime switching should go through `model_config` or `/model`, not ad-hoc env vars.
- Content-semantic decisions must stay AI-native. Intent routing, task-vs-chat classification, follow-up/task-tracking decisions, and similar meaning-based judgments must be made by model-driven logic, not by hardcoded regex/keyword/heuristic shortcuts.
- LLM usage accounting is aggregated by `day + session + model`; do not reintroduce append-only `events.jsonl` for this path.
- Async-first test style: `pytest` with `asyncio_mode=auto`; most core/manager tests are `@pytest.mark.asyncio`.
- Project packaging uses `pyproject.toml` + `hatchling`; no `package.json` or JS build pipeline.

## ANTI-PATTERNS (THIS PROJECT)
- Do not route regular user execution into Core Manager business logic (`DEVELOPMENT.md`).
- Do not bypass `state_store`/`state_paths` with ad-hoc file paths.
- Do not reintroduce a separate manager/worker execution split, shared queue, or stale `worker_*` runtime assumptions.
- Do not move new aggregate runtime data back to unbounded JSONL logs when SQLite tables already fit the workload.
- Do not make the model fetch image URLs itself when deterministic preprocessing can resolve them at the handler layer.
- Do not add hardcoded content classifiers or shortcut branches for semantic user intent, such as greeting detection, task routing, or whether a request deserves task tracking. If the decision depends on meaning, it must go through AI-native routing/policy logic.
- Do not upgrade to heavy coding tools when read/write/bash/browser primitives already solve the task (`DEVELOPMENT.md`).

## UNIQUE STYLES
- Mixed-language docs are normal (English + Chinese) in architecture and feature docs.
- Platform support is parallel by design (Telegram/Discord/DingTalk/Web), not plugin-afterthought.
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
