# CORE KNOWLEDGE BASE

## OVERVIEW
`src/core/` owns manager orchestration, tool/runtime composition, canonical state I/O, prompt policy, memory, and heartbeat governance.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Tool assembly and execution loop | `src/core/agent_orchestrator.py`, `src/core/orchestrator_runtime_tools.py`, `src/core/primitive_runtime.py` | merges core/manager/extension tools and drives call loop |
| Tool definitions, broker, and access gating | `src/core/tool_registry.py`, `src/core/tool_broker.py`, `src/core/tool_access_store.py` | runtime-visible tool set, access policy, compatibility surface |
| Extension execution pipeline | `src/core/extension_router.py`, `src/core/extension_executor.py`, `src/core/tools/extension_tools.py`, `src/core/skill_loader.py` | skill routing, execution, and contract adaptation |
| Prompt policy and personality chain | `src/core/prompt_composer.py`, `src/core/prompts.py`, `src/core/soul_store.py`, `src/core/model_config.py` | system constraints + SOUL + model choices |
| Orchestrator context and event closure | `src/core/orchestrator_context.py`, `src/core/orchestrator_event_handler.py` | context snapshots and delivery completion handling |
| Task lifecycle and heartbeat | `src/core/task_inbox.py`, `src/core/task_manager.py`, `src/core/heartbeat_worker.py`, `src/core/heartbeat_store.py` | state transitions and periodic maintenance |
| Canonical state persistence | `src/core/state_store.py`, `src/core/state_paths.py`, `src/core/state_io.py`, `src/core/state_file.py`, `src/core/audit_store.py` | user/system state protocol and storage primitives |
| Memory and kernel snapshots | `src/core/markdown_memory_store.py`, `src/core/kernel_config_store.py` | persistent context and runtime config history |
| Platform abstraction layer | `src/core/platform/adapter.py`, `src/core/platform/registry.py`, `src/core/platform/models.py` | unified adapter API for multi-platform handlers |

## CONVENTIONS
- Keep Core Manager as orchestrator/governor; do not execute user business workflows directly in core loops.
- Add tools via `tool_registry` + runtime merge path; keep tool names/signatures stable when possible.
- Use `state_paths`/`state_io`/`state_store` for persistence; avoid ad-hoc direct path writes.
- Preserve explicit task source/state transitions and loop guardrails across orchestration modules.

## ANTI-PATTERNS
- Don't bypass `orchestrator_runtime_tools`/`tool_registry` with direct tool wiring.
- Don't mix worker-kernel execution concerns into manager orchestration control paths.
- Don't weaken retry boundaries, timeout handling, or loop guards in agent/tool loops.
- Don't read/write state payloads outside canonical helpers (`state_file.py`, `state_io.py`).

## QUICK CHECKS
```bash
uv run pytest tests/core/test_orchestrator_single_loop.py
uv run pytest tests/core/test_orchestrator_delivery_closure.py
uv run pytest tests/core/test_orchestrator_runtime_tools.py
uv run pytest tests/core/test_prompt_composer.py
```
