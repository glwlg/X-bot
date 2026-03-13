# AI-Native Task Closure Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep user tasks open until their real closure condition is satisfied, and let heartbeat drive manager-led review of unfinished work through generic task state semantics.

**Architecture:** Add a generic unfinished-task state model centered on `waiting_external` plus follow-up metadata, expose a manager-side `task_tracker` tool for listing and updating tasks, then change orchestrator completion rules so manager can intentionally leave tasks open for heartbeat-driven follow-up. Heartbeat remains generic: it asks manager to review unfinished tasks rather than running a hardcoded PR tracker.

**Tech Stack:** Python 3.14, asyncio, file-backed task state, heartbeat runtime, manager direct tools, pytest/pytest-asyncio

---

Spec: `docs/superpowers/specs/2026-03-13-ai-native-task-closure-design.md`

## Chunk 1: State Model And Manager Tooling

### Task 1: Add unfinished-task state semantics to task persistence

**Files:**
- Test: `tests/core/test_task_inbox.py`
- Modify: `src/core/task_inbox.py`
- Modify: `src/core/session_task_store.py`

- [ ] **Step 1: Write failing task-inbox tests for `waiting_external` and open-task listing**

```python
@pytest.mark.asyncio
async def test_task_inbox_keeps_waiting_external_open(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)
    task = await inbox.submit(source="user_chat", goal="follow up PR", user_id="u-1")

    await inbox.update_status(
        task.task_id,
        "waiting_external",
        metadata={
            "followup": {"done_when": "PR merged"},
        },
    )

    stored = await inbox.get(task.task_id)
    assert stored.status == "waiting_external"
```

- [ ] **Step 2: Run the focused task-inbox tests to verify they fail**

Run: `uv run pytest tests/core/test_task_inbox.py -q`
Expected: FAIL because `waiting_external` is not a recognized status and no open-task helper exists.

- [ ] **Step 3: Implement `waiting_external` normalization and metadata-preserving updates**

```python
if token in {"pending", "planning", "running", "waiting_user", "waiting_external", "completed", "failed", "cancelled"}:
    return token
```

- [ ] **Step 4: Add a generic open-task listing helper used by future manager tooling**

```python
async def list_open(...):
    open_states = {"pending", "planning", "running", "waiting_user", "waiting_external"}
    ...
```

- [ ] **Step 5: Re-run the focused task-inbox tests**

Run: `uv run pytest tests/core/test_task_inbox.py -q`
Expected: PASS

### Task 2: Preserve session active-task state for `waiting_external`

**Files:**
- Test: `tests/core/test_session_task_store.py`
- Test: `tests/core/test_orchestrator_delivery_closure.py`
- Modify: `src/core/heartbeat_store.py`
- Modify: `src/core/orchestrator_event_handler.py`
- Modify: `src/core/agent_orchestrator.py`

- [ ] **Step 1: Write failing tests for session-state preservation**

```python
@pytest.mark.asyncio
async def test_waiting_external_task_is_not_cleared_from_session_state(...):
    ...
    assert active["status"] == "waiting_external"
```

- [ ] **Step 2: Run the session/closure regressions to verify they fail**

Run: `uv run pytest tests/core/test_session_task_store.py tests/core/test_orchestrator_delivery_closure.py -q`
Expected: FAIL because final response still clears active task state.

- [ ] **Step 3: Teach session-state update paths to preserve `waiting_external`**

```python
terminal_statuses = {"done", "failed", "cancelled", "timed_out"}
```

plus explicit final-response handling that keeps session/task state open when current status is `waiting_external`.

- [ ] **Step 4: Re-run the session/closure regressions**

Run: `uv run pytest tests/core/test_session_task_store.py tests/core/test_orchestrator_delivery_closure.py -q`
Expected: PASS

### Task 3: Add a manager-side `task_tracker` direct tool

**Files:**
- Create: `src/core/task_tracker_service.py`
- Create: `src/core/tools/task_tracker_tools.py`
- Modify: `src/core/skill_tool_handlers.py`
- Modify: `src/core/prompt_composer.py`
- Create: `skills/builtin/task_tracker/SKILL.md`
- Test: `tests/core/test_task_tracker_service.py`
- Test: `tests/core/test_orchestrator_runtime_tools.py`

- [ ] **Step 1: Write failing tests for list/get/update behavior**

```python
@pytest.mark.asyncio
async def test_task_tracker_lists_open_tasks(tmp_path):
    ...
    result = await service.list_open(user_id="u-1")
    assert result["data"]["tasks"]


@pytest.mark.asyncio
async def test_task_tracker_update_can_mark_waiting_external_and_store_followup(tmp_path):
    ...
    result = await service.update(..., status="waiting_external", done_when="PR merged")
    assert result["data"]["task"]["status"] == "waiting_external"


@pytest.mark.asyncio
async def test_task_tracker_get_returns_followup_metadata(tmp_path):
    ...
    assert result["data"]["task"]["metadata"]["followup"]["done_when"] == "PR merged"
```

- [ ] **Step 2: Run the new `task_tracker` tests to verify they fail**

Run: `uv run pytest tests/core/test_task_tracker_service.py -q`
Expected: FAIL because the service and tool do not exist yet.

- [ ] **Step 3: Implement generic list/get/update operations with namespaced follow-up metadata**

```python
metadata["followup"] = {
    "done_when": done_when,
    "next_review_after": next_review_after,
    "refs": refs,
    "notes": notes,
    "announce_before_action": announce_before_action,
}
```

- [ ] **Step 4: Support optional proactive announcement before follow-up work**

```python
if announce_text:
    await push_background_text(platform=platform, chat_id=chat_id, text=announce_text)
```

- [ ] **Step 5: Add runtime exposure coverage for the new direct tool**

```python
assert "task_tracker" in dispatcher.available_tool_names
```

- [ ] **Step 6: Re-run the new task-tracker tests**

Run: `uv run pytest tests/core/test_task_tracker_service.py tests/core/test_orchestrator_runtime_tools.py -q`
Expected: PASS

## Chunk 2: Orchestrator And Prompt Semantics

### Task 4: Prevent final-response auto-completion for intentionally open tasks

- [ ] **Step 1: Write a failing regression test for `waiting_external` final responses**

```python
@pytest.mark.asyncio
async def test_final_response_does_not_complete_waiting_external_task(...):
    ...
    assert stored.status == "waiting_external"
```

- [ ] **Step 2: Run the focused closure tests to verify they fail**

Run: `uv run pytest tests/core/test_orchestrator_delivery_closure.py -q`
Expected: FAIL because final responses currently force `completed`.

- [ ] **Step 3: Teach orchestrator completion logic to respect intentionally open task states**

```python
if current_status == "waiting_external":
    await task_inbox.update_status(..., "waiting_external", ...)
    self.flags.completed = True
    return
```

- [ ] **Step 4: Re-run the closure regression tests**

Run: `uv run pytest tests/core/test_orchestrator_delivery_closure.py -q`
Expected: PASS

### Task 5: Teach manager the AI-native closure model

**Files:**
- Modify: `src/core/prompts.py`
- Modify: `DEVELOPMENT.md`
- Test: `tests/core/test_prompt_composer.py`

- [ ] **Step 1: Add a failing prompt test for unfinished-task guidance**

```python
def test_manager_prompt_mentions_waiting_external_and_task_tracker(...):
    assert "waiting_external" in prompt
    assert "task_tracker" in prompt
```

- [ ] **Step 2: Run the prompt tests to verify they fail**

Run: `uv run pytest tests/core/test_prompt_composer.py -q`
Expected: FAIL because the new guidance is absent.

- [ ] **Step 3: Update manager prompt and development doc with the AI-native closure rules**

```text
- completion means closure, not merely one response round
- use `task_tracker` to inspect and update unfinished tasks
- `waiting_external` stays open for heartbeat-driven review
- before automatic follow-up from heartbeat, announce the action to the user
```

- [ ] **Step 4: Re-run the prompt tests**

Run: `uv run pytest tests/core/test_prompt_composer.py -q`
Expected: PASS

## Chunk 3: Heartbeat-Driven Review Of Unfinished Tasks

### Task 6: Let heartbeat-driven manager runs review unfinished tasks generically

**Files:**
- Test: `tests/core/test_heartbeat_worker.py`
- Modify: `src/core/heartbeat_worker.py`

- [ ] **Step 1: Write a failing heartbeat regression test for unfinished-task review goals**

```python
@pytest.mark.asyncio
async def test_heartbeat_goal_can_review_open_tasks_without_pr_specific_logic(...):
    ...
    assert "task_tracker" in seen_tools
```

- [ ] **Step 2: Run the focused heartbeat tests to verify they fail**

Run: `uv run pytest tests/core/test_heartbeat_worker.py -q`
Expected: FAIL because heartbeat lacks unfinished-task review guidance and related coverage.

- [ ] **Step 3: Add generic heartbeat prompt guidance for reviewing unfinished work**

```text
If the heartbeat goal is about unfinished work, inspect open tasks first and then decide which one to advance.
```

Do not add PR-specific or unfinished-task-specific branching logic to heartbeat execution; keep the checklist-to-manager path generic.

- [ ] **Step 4: Re-run the heartbeat tests**

Run: `uv run pytest tests/core/test_heartbeat_worker.py -q`
Expected: PASS

### Task 7: Cover announcement-before-action behavior

**Files:**
- Test: `tests/core/test_task_tracker_service.py`
- Modify: `src/core/task_tracker_service.py`

- [ ] **Step 1: Add failing tests for announcement ordering and no-op observation updates**

```python
@pytest.mark.asyncio
async def test_task_tracker_announce_text_records_send_and_suppresses_duplicate_key(...):
    ...


@pytest.mark.asyncio
async def test_task_tracker_observation_update_without_announce_does_not_push(...):
    ...
```

- [ ] **Step 2: Run the task-tracker tests to verify they fail**

Run: `uv run pytest tests/core/test_task_tracker_service.py -q`
Expected: FAIL because announcement audit behavior is not implemented yet.

- [ ] **Step 3: Implement announcement audit fields and duplicate suppression**

```python
if announce_key and followup.get("last_announcement_key") == announce_key:
    send = False
```

- [ ] **Step 4: Re-run the task-tracker tests**

Run: `uv run pytest tests/core/test_task_tracker_service.py -q`
Expected: PASS

## Chunk 4: Verification

### Task 8: Run the focused regression suite for the AI-native closure path

**Files:**
- Test: `tests/core/test_task_inbox.py`
- Test: `tests/core/test_task_tracker_service.py`
- Test: `tests/core/test_session_task_store.py`
- Test: `tests/core/test_orchestrator_delivery_closure.py`
- Test: `tests/core/test_prompt_composer.py`
- Test: `tests/core/test_heartbeat_worker.py`
- Test: `tests/core/test_orchestrator_runtime_tools.py`

- [ ] **Step 1: Run the focused suite**

Run: `uv run pytest tests/core/test_task_inbox.py tests/core/test_task_tracker_service.py tests/core/test_session_task_store.py tests/core/test_orchestrator_delivery_closure.py tests/core/test_prompt_composer.py tests/core/test_heartbeat_worker.py tests/core/test_orchestrator_runtime_tools.py -q`
Expected: PASS

- [ ] **Step 2: Run adjacent manager loop regressions**

Run: `uv run pytest tests/core/test_ai_service_loop_guard.py tests/core/test_ai_handlers_dispatch.py tests/core/test_orchestrator_runtime_tools.py -q`
Expected: PASS

- [ ] **Step 3: Commit the implementation**

```bash
git add DEVELOPMENT.md docs/superpowers/specs/2026-03-13-ai-native-task-closure-design.md docs/superpowers/plans/2026-03-13-ai-native-task-closure.md src/core/task_inbox.py src/core/task_tracker_service.py src/core/tools/task_tracker_tools.py src/core/skill_tool_handlers.py src/core/orchestrator_event_handler.py src/core/agent_orchestrator.py src/core/prompts.py src/core/heartbeat_worker.py src/core/heartbeat_store.py skills/builtin/task_tracker/SKILL.md tests/core/test_task_inbox.py tests/core/test_task_tracker_service.py tests/core/test_session_task_store.py tests/core/test_orchestrator_delivery_closure.py tests/core/test_prompt_composer.py tests/core/test_heartbeat_worker.py tests/core/test_orchestrator_runtime_tools.py
git commit -m "feat: add ai-native unfinished task closure"
```
