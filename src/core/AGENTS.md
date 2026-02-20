# CORE KNOWLEDGE BASE

## OVERVIEW
`src/core/` owns orchestration, tool routing, prompt composition, memory stores, and worker governance.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Tool injection and runtime dispatch | `src/core/agent_orchestrator.py`, `src/core/orchestrator_runtime_tools.py` | merges core/manager/extension tools and executes call path |
| Tool definitions and profile gating | `src/core/tool_registry.py`, `src/core/tool_access_store.py` | `run_extension` and memory/tool restrictions live here |
| Extension execution pipeline | `src/core/extension_router.py`, `src/core/extension_executor.py`, `src/core/tools/extension_tools.py` | central skill call and execution adapter layer |
| Prompt policy and personality chain | `src/core/prompt_composer.py`, `src/core/prompts.py`, `src/core/soul_store.py` | system constraints + SOUL injection |
| Task lifecycle and heartbeat | `src/core/task_inbox.py`, `src/core/task_manager.py`, `src/core/heartbeat_worker.py`, `src/core/heartbeat_store.py` | state transitions and periodic maintenance |
| Memory and kernel state | `src/core/markdown_memory_store.py`, `src/core/kernel_config_store.py` | persistent context and kernel-level config |

## CONVENTIONS
- Keep Core Manager as orchestrator/governor; avoid embedding user-facing business execution here.
- Prefer primitive tool path first; escalate to heavier toolchains only when primitives cannot solve.
- Preserve explicit task state transitions and source labeling across orchestration components.
- Treat `tool_registry` + runtime tool merge as compatibility-sensitive API surface.

## ANTI-PATTERNS
- Don't bypass `orchestrator_runtime_tools`/`tool_registry` with ad-hoc direct tool wiring.
- Don't mix worker-only execution concerns back into manager orchestration control paths.
- Don't weaken loop guards, retry boundaries, or timeout handling in agent/tool loops.
- Don't add memory/storage shortcuts that skip repository/store abstractions.

## QUICK CHECKS
```bash
uv run pytest tests/core/test_orchestrator_single_loop.py
uv run pytest tests/core/test_orchestrator_delivery_closure.py
uv run pytest tests/core/test_ai_service_retry_loop.py
```
