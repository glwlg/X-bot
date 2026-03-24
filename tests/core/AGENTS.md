# CORE TESTS KNOWLEDGE BASE

## OVERVIEW
`tests/core/` validates manager orchestration loops, tool dispatch contracts, subagent/background runtime behavior, and core reliability boundaries.

## WHERE TO LOOK
| Behavior | Location | Notes |
|----------|----------|-------|
| Orchestrator loop semantics | `tests/core/test_orchestrator_single_loop.py`, `tests/core/test_orchestrator_delivery_closure.py`, `tests/core/test_orchestrator_runtime_tools.py`, `tests/core/test_orchestrator_context.py` | primary regression suites for manager loop behavior and closure |
| AI loop guardrails and retry | `tests/core/test_ai_service_loop_guard.py`, `tests/core/test_ai_service_retry_loop.py` | repetition control, recovery, and retry boundaries |
| Extension and runtime contracts | `tests/core/test_extension_router.py`, `tests/core/test_extension_executor.py`, `tests/core/test_skill_arg_planner.py`, `tests/core/test_skill_loader_schema_inference.py` | tool-call and skill execution compatibility |
| Handler-level routing guarantees | `tests/core/test_ai_handlers_dispatch.py`, `tests/core/test_heartbeat_handlers.py`, `tests/core/test_start_handlers_stop.py`, `tests/core/test_unified_context_reply.py` | command/message routing and control-path behavior |
| Background heartbeat behavior | `tests/core/test_heartbeat_worker.py`, `tests/core/test_heartbeat_handlers.py` | periodic checks, push delivery, and runtime state |
| Prompt, memory, and state stores | `tests/core/test_prompt_composer.py`, `tests/core/test_markdown_memory_store.py`, `tests/core/test_soul_store.py` | prompt chain integrity and persistence behavior |
| Builtin-skill regressions | `tests/core/test_web_search_execute.py`, `tests/core/test_rss_subscribe_execute.py`, `tests/core/test_generate_image_skill.py`, `tests/core/test_deployment_manager_auto_deploy.py` | high-usage skill execution paths |
| Module and policy surface checks | `tests/core/test_core_module_usage.py`, `tests/core/test_agents_init.py`, `tests/core/test_tool_registry_pi_mode.py`, `tests/core/test_tool_access_store.py` | import/API invariants and access policy coverage |

## CONVENTIONS
- Async is the default: use `@pytest.mark.asyncio` for core behavior tests.
- Keep suites scenario-focused (dispatch, closure, retry, heartbeat, relay), not broad end-to-end scripts.
- Use deterministic fixtures/mocks for tool results and state transitions.
- Prefer assertions on structured state/payload fields over incidental log text.
- For filesystem state tests, isolate paths per test to avoid cross-test contamination.

## ANTI-PATTERNS
- Don't rewrite focused core tests into large integration-style flows.
- Don't silently broaden acceptance criteria for loop guards or retry behavior.
- Don't couple assertions to unstable timestamps/order when deterministic fields exist.

## RUNNING
```bash
uv run pytest tests/core
uv run pytest tests/core/test_orchestrator_single_loop.py tests/core/test_heartbeat_worker.py
```
