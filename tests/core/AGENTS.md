# CORE TESTS KNOWLEDGE BASE

## OVERVIEW
`tests/core/` validates orchestration, runtime loops, dispatch behavior, and core reliability boundaries.

## WHERE TO LOOK
| Behavior | Location | Notes |
|----------|----------|-------|
| Single-loop orchestration semantics | `tests/core/test_orchestrator_single_loop.py` | highest-signal regression suite for manager loop behavior |
| Delivery closure and completion criteria | `tests/core/test_orchestrator_delivery_closure.py` | verifies when/why tasks close |
| Runtime dispatch and tool paths | `tests/core/test_dispatch_tools.py`, `tests/core/test_extension_executor.py` | extension and tool execution contracts |
| Worker runtime behavior | `tests/core/test_worker_runtime.py`, `tests/core/test_worker_store.py`, `tests/core/test_worker_task_file_store.py` | daemon/runtime state and file-lock semantics |
| Heartbeat lifecycle | `tests/core/test_heartbeat_worker.py`, `tests/core/test_heartbeat_store.py`, `tests/core/test_heartbeat_handlers.py` | periodic maintenance and state visibility |
| Guardrails in AI loop | `tests/core/test_ai_service_loop_guard.py`, `tests/core/test_ai_service_retry_loop.py` | repetition control and recovery logic |

## CONVENTIONS
- Async is the default: use `@pytest.mark.asyncio` for core behavior tests.
- Keep tests scenario-focused (dispatch, closure, retry, heartbeat), not broad integration scripts.
- Use deterministic fixtures/mocks for tool results and state transitions.
- Treat timing-sensitive worker/heartbeat tests as contract tests; avoid loose assertions.

## ANTI-PATTERNS
- Don't rewrite core tests into end-to-end style when validating a narrow orchestration rule.
- Don't silently broaden acceptance criteria for loop guards or retry behavior.
- Don't couple test assertions to incidental log strings when state fields are available.

## RUNNING
```bash
uv run pytest tests/core
```
