# Metadata And Relay Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce validated delivery/session metadata contracts and simplify the worker result relay hot path so delivery correctness is no longer coupled to optional enrichment or loose dict keys.

**Architecture:** Add minimal typed contract models in `src/shared/contracts/`, normalize legacy metadata at dispatch/runtime boundaries, and then refactor `WorkerResultRelay` into clearer phases: target resolution, payload delivery, delivery persistence, and post-delivery side effects. Use `delivery_store` as the canonical ledger instead of split-brain `_relay` metadata.

**Tech Stack:** Python dataclasses/Pydantic-style validation, asyncio, shared queue contracts, pytest/pytest-asyncio

---

Spec: `docs/superpowers/specs/2026-03-12-delivery-reliability-and-state-boundaries-design.md`

Scope: This is the Phase-4 plan for spec Workstreams E-F. It assumes the Phase-2 proactive-target contract and heuristic-target removal from `2026-03-12-state-and-proactive-delivery.md` are already merged.

Prerequisites/Start Gates:

- proactive target guessing is already removed and covered by passing regressions;
- the Phase-2 compatibility layer already normalizes legacy top-level keys into a minimal delivery-target contract;
- do not delete legacy helper reads until a compatibility shim or caller inventory proves no remaining caller depends on them.

## File Map

- Create: `src/shared/contracts/delivery_metadata.py` - validated delivery target metadata and compatibility normalization
- Create: `src/shared/contracts/session_metadata.py` - validated session/task-inbox/closure metadata accessors
- Modify: `src/core/skill_tool_handlers.py` - normalize runtime metadata into typed contracts before dispatch
- Modify: `src/manager/dispatch/service.py` - submit typed delivery/session metadata into task envelopes
- Modify: `src/manager/relay/result_relay.py` - consume typed metadata and split hot path from follow-up effects
- Modify: `src/manager/relay/closure_service.py` - consume typed session metadata instead of scattered dict keys
- Modify: `src/manager/relay/progress_relay.py` - align session id lookup with typed contract helpers
- Modify: `src/shared/queue/dispatch_queue.py` - stop treating `_relay` as canonical delivery state
- Modify: `src/core/tools/dispatch_tools.py` - report canonical delivery health only from `delivery_store`
- Test: `tests/shared/test_delivery_metadata.py`
- Modify: `tests/manager/test_dispatch_service.py`
- Modify: `tests/core/test_worker_result_relay.py`
- Modify: `tests/core/test_worker_result_relay_heartbeat.py`
- Modify: `tests/core/test_delivery_store.py`
- Modify: `tests/core/test_dispatch_tools.py`
- Modify: `tests/core/test_orchestrator_delivery_closure.py`
- Modify: `tests/shared/test_dispatch_queue.py`

## Chunk 1: Typed Metadata Contracts And Relay Separation

### Task 1: Add failing tests for metadata normalization and explicit validation

**Files:**
- Create: `tests/shared/test_delivery_metadata.py`
- Modify: `tests/manager/test_dispatch_service.py`
- Modify: `tests/core/test_worker_result_relay.py`

- [ ] **Step 1: Write failing metadata-contract tests**

```python
def test_delivery_metadata_rejects_cross_user_target_guessing():
    with pytest.raises(ValueError, match="cross-user"):
        DeliveryMetadata.from_legacy(
            {"user_id": "1001", "chat_id": "2002", "platform": "telegram"},
            require_explicit_target=True,
        )


def test_session_metadata_prefers_task_inbox_then_session_task_id():
    meta = SessionMetadata.from_legacy({"task_inbox_id": "ibox-1", "session_task_id": "session-9"})
    assert meta.primary_session_id == "ibox-1"


def test_delivery_metadata_rejects_unknown_version():
    with pytest.raises(ValueError, match="version"):
        DeliveryMetadata.from_dict({"version": "v999", "owner_user_id": "1001", "platform": "telegram", "target_chat_id": "257675041"})


def test_session_metadata_rejects_unknown_version():
    with pytest.raises(ValueError, match="version"):
        SessionMetadata.from_dict({"version": "v999", "task_inbox_id": "ibox-1"})


def test_metadata_serializes_under_stable_namespaces():
    payload = build_metadata_payload(...)
    assert "delivery" in payload
    assert "session" in payload


def test_malformed_namespaced_payload_is_rejected_explicitly():
    with pytest.raises(ValueError, match="delivery"):
        DeliveryMetadata.from_any({"delivery": "not-a-dict"})
```

- [ ] **Step 2: Run the metadata tests to verify they fail**

Run: `uv run pytest tests/shared/test_delivery_metadata.py tests/manager/test_dispatch_service.py tests/core/test_worker_result_relay.py -k "metadata or cross_user or session" -v`
Expected: FAIL because no typed metadata module exists and relay still reads raw dict keys directly.

- [ ] **Step 3: Define minimal typed metadata contracts**

```python
@dataclass(frozen=True)
class DeliveryMetadata:
    version: str
    owner_user_id: str
    platform: str
    target_chat_id: str
    task_inbox_id: str = ""
    session_task_id: str = ""

    @classmethod
    def from_legacy(cls, raw: Mapping[str, Any], *, require_explicit_target: bool = False) -> "DeliveryMetadata":
        ...


@dataclass(frozen=True)
class SessionMetadata:
    version: str
    task_inbox_id: str
    session_task_id: str

    @classmethod
    def from_legacy(cls, raw: Mapping[str, Any]) -> "SessionMetadata":
        ...
```

- [ ] **Step 4: Normalize dispatch/runtime metadata at the boundaries**

```python
metadata_obj["delivery"] = DeliveryMetadata.from_runtime_context(...).to_dict()
metadata_obj["session"] = SessionMetadata.from_runtime_context(...).to_dict()
```

Consumers must reject malformed `metadata["delivery"]` or `metadata["session"]` payloads explicitly instead of silently falling back to raw top-level keys.

- [ ] **Step 5: Add a compatibility inventory task before strict consumer cleanup**

Run: `uv run python - <<'PY'
from pathlib import Path
import re

root = Path('src')
pattern = re.compile(r'metadata\.get\("(platform|chat_id|user_id|task_inbox_id|session_task_id)"')
for path in root.rglob('*.py'):
    text = path.read_text(encoding='utf-8')
    if pattern.search(text):
        print(path)
PY`
Expected: a concrete caller list to migrate or shield with compatibility helpers before removing raw-key reads.

- [ ] **Step 6: Re-run metadata and dispatch tests**

Run: `uv run pytest tests/shared/test_delivery_metadata.py tests/manager/test_dispatch_service.py -v`
Expected: PASS, with dispatch emitting normalized metadata namespaces.

- [ ] **Step 7: Commit the typed metadata baseline**

```bash
git add src/shared/contracts/delivery_metadata.py src/shared/contracts/session_metadata.py src/core/skill_tool_handlers.py src/manager/dispatch/service.py tests/shared/test_delivery_metadata.py tests/manager/test_dispatch_service.py
git commit -m "feat: add typed delivery and session metadata"
```

### Task 2: Prove legacy-key compatibility before strict consumer cleanup

**Files:**
- Modify: `tests/core/test_worker_result_relay.py`
- Modify: `tests/manager/test_dispatch_service.py`
- Modify: `src/manager/dispatch/service.py`
- Modify: `src/core/skill_tool_handlers.py`

- [ ] **Step 1: Add failing compatibility tests for legacy envelopes**

```python
@pytest.mark.asyncio
async def test_legacy_top_level_delivery_keys_are_normalized_before_relay_reads(monkeypatch):
    task = make_task(metadata={"platform": "telegram", "chat_id": "257675041", "user_id": "1001"})
    normalized = relay._delivery_metadata(task)
    assert normalized.target_chat_id == "257675041"
```

- [ ] **Step 2: Run the compatibility tests to verify they fail**

Run: `uv run pytest tests/manager/test_dispatch_service.py tests/core/test_worker_result_relay.py -k "legacy_top_level or normalized or namespace" -v`
Expected: FAIL until legacy-key normalization is wired ahead of strict typed reads.

- [ ] **Step 3: Add compatibility normalization at every boundary entry**

```python
delivery_meta = DeliveryMetadata.from_any(task.metadata or {})
session_meta = SessionMetadata.from_any(task.metadata or {})
```

- [ ] **Step 4: Re-run the compatibility tests**

Run: `uv run pytest tests/manager/test_dispatch_service.py tests/core/test_worker_result_relay.py -k "legacy_top_level or normalized" -v`
Expected: PASS.

- [ ] **Step 5: Commit the compatibility layer**

```bash
git add src/manager/dispatch/service.py src/core/skill_tool_handlers.py tests/manager/test_dispatch_service.py tests/core/test_worker_result_relay.py
git commit -m "fix: normalize legacy delivery metadata before strict validation"
```

### Task 3: Refactor relay to separate delivery from post-delivery side effects

**Files:**
- Modify: `src/manager/relay/result_relay.py`
- Modify: `src/manager/relay/closure_service.py`
- Modify: `src/manager/relay/progress_relay.py`
- Modify: `tests/core/test_worker_result_relay.py`
- Modify: `tests/core/test_worker_result_relay_heartbeat.py`
- Modify: `tests/core/test_orchestrator_delivery_closure.py`

- [ ] **Step 1: Add failing relay tests that protect the hot-path boundary**

```python
@pytest.mark.asyncio
async def test_successful_delivery_is_not_reversed_by_summary_failure(monkeypatch):
    monkeypatch.setattr(relay, "_deliver_task", AsyncMock(return_value=True))
    monkeypatch.setattr(relay, "_run_post_delivery_effects", AsyncMock(side_effect=RuntimeError("boom")))

    await relay.process_once()

    delivery_store.mark_delivered.assert_awaited()


@pytest.mark.asyncio
async def test_closure_followup_failure_keeps_delivery_state_delivered(monkeypatch):
    ...


@pytest.mark.asyncio
async def test_progress_ack_failure_does_not_downgrade_successful_delivery(monkeypatch):
    ...


@pytest.mark.asyncio
async def test_session_sync_failure_does_not_downgrade_successful_delivery(monkeypatch):
    ...
```

- [ ] **Step 2: Run the relay boundary tests to verify they fail**

Run: `uv run pytest tests/core/test_worker_result_relay.py tests/core/test_worker_result_relay_heartbeat.py tests/core/test_orchestrator_delivery_closure.py -k "summary_failure or followup_failure or progress_ack_failure or session_sync_failure or delivery_state" -v`
Expected: FAIL because relay still mixes delivery persistence and side-effect execution.

- [ ] **Step 3: Extract explicit post-delivery side-effect handling**

```python
delivered = await self._deliver_task(...)
if delivered:
    await delivery_store.mark_delivered(...)
    await dispatch_queue.mark_delivered(task.task_id)
    await self._sync_session_delivery_state(...)
    await self._run_post_delivery_effects(task=task, result=result_dict, prepared=prepared)
```

- [ ] **Step 4: Convert closure and progress helpers to typed session metadata access**

```python
session_meta = SessionMetadata.from_task_metadata(task.metadata or {})
task_inbox_id = session_meta.task_inbox_id
session_task_id = session_meta.primary_session_id
```

- [ ] **Step 5: Re-run relay and closure tests**

Run: `uv run pytest tests/core/test_worker_result_relay.py tests/core/test_worker_result_relay_heartbeat.py tests/core/test_orchestrator_delivery_closure.py -v`
Expected: PASS, proving user-visible delivery outcome survives post-delivery failures.

- [ ] **Step 6: Commit the relay hot-path split**

```bash
git add src/manager/relay/result_relay.py src/manager/relay/closure_service.py src/manager/relay/progress_relay.py tests/core/test_worker_result_relay.py tests/core/test_orchestrator_delivery_closure.py
git commit -m "refactor: split relay delivery from follow-up effects"
```

### Task 4: Remove `_relay` split-brain status from queue health paths

**Files:**
- Modify: `src/shared/queue/dispatch_queue.py`
- Modify: `src/core/tools/dispatch_tools.py`
- Modify: `tests/shared/test_dispatch_queue.py`
- Modify: `tests/core/test_delivery_store.py`

- [ ] **Step 1: Add failing queue-health tests that require canonical delivery_store values**

```python
@pytest.mark.asyncio
async def test_dispatch_queue_delivery_health_does_not_read_relay_metadata_when_delivery_store_exists(...):
    ...
    assert payload["dead_letter"] == 1
    assert payload["source"] == "delivery_store"
```

- [ ] **Step 2: Run the delivery-health tests to verify they fail**

Run: `uv run pytest tests/shared/test_dispatch_queue.py tests/core/test_delivery_store.py -k "delivery_health" -v`
Expected: FAIL because `dispatch_queue.delivery_health()` still derives retry/dead-letter state from `_relay` metadata.

- [ ] **Step 3: Change queue/tool health reporting to treat `delivery_store` as canonical**

```python
async def worker_status(...):
    delivery_health = await delivery_store.delivery_health(...)
    return {"delivery_health": delivery_health, "summary": _format_summary(delivery_health, worker_metrics)}
```

- [ ] **Step 4: Keep queue-level compatibility read-only until all callers are migrated**

```python
def _relay_meta(task: TaskEnvelope) -> Dict[str, Any]:
    metadata = dict(task.metadata or {})
    relay = metadata.get("_relay")
    return dict(relay) if isinstance(relay, dict) else {}
```

Keep this helper only as a compatibility shim until the caller inventory from Task 1 shows no remaining runtime dependency. New health/status logic must not treat it as canonical.

- [ ] **Step 5: Re-run shared queue and delivery-store tests**

Run: `uv run pytest tests/shared/test_dispatch_queue.py tests/core/test_delivery_store.py tests/core/test_dispatch_tools.py -v`
Expected: PASS, with a single delivery ledger driving health summaries.

- [ ] **Step 6: Commit the canonical ledger cleanup**

```bash
git add src/shared/queue/dispatch_queue.py src/core/tools/dispatch_tools.py tests/shared/test_dispatch_queue.py tests/core/test_delivery_store.py
git commit -m "refactor: use canonical delivery ledger for health reporting"
```

### Task 5: Run the final hardening suite

**Files:**
- Test: `tests/shared/test_delivery_metadata.py`
- Test: `tests/manager/test_dispatch_service.py`
- Test: `tests/core/test_worker_result_relay.py`
- Test: `tests/core/test_worker_result_relay_heartbeat.py`
- Test: `tests/core/test_delivery_store.py`
- Test: `tests/core/test_dispatch_tools.py`
- Test: `tests/core/test_orchestrator_delivery_closure.py`
- Test: `tests/shared/test_dispatch_queue.py`

- [ ] **Step 1: Run the full metadata and relay regression suite**

Run: `uv run pytest tests/shared/test_delivery_metadata.py tests/manager/test_dispatch_service.py tests/core/test_worker_result_relay.py tests/core/test_worker_result_relay_heartbeat.py tests/core/test_delivery_store.py tests/core/test_dispatch_tools.py tests/core/test_orchestrator_delivery_closure.py tests/shared/test_dispatch_queue.py -v`
Expected: PASS.

- [ ] **Step 2: Run the cross-cutting delivery suite once more**

Run: `uv run pytest tests/core/test_scheduler_rss_links.py tests/core/test_background_delivery.py tests/core/test_heartbeat_worker.py tests/core/test_telegram_adapter_send_message.py tests/core/test_worker_result_relay.py tests/shared/test_dispatch_queue.py -v`
Expected: PASS, confirming proactive and relay delivery share stable semantics.

- [ ] **Step 3: Commit the final hardening pass**

```bash
git add tests/shared/test_delivery_metadata.py tests/manager/test_dispatch_service.py tests/core/test_worker_result_relay.py tests/core/test_orchestrator_delivery_closure.py tests/shared/test_dispatch_queue.py
git commit -m "test: harden metadata and relay delivery boundaries"
```
