# AI-Native Task Closure Design

Date: 2026-03-13
Status: Draft reviewed with user in chat
Scope: Manager, heartbeat, task inbox, session state, manager tool guidance

## Background

The current manager loop treats a task as complete when one execution round produces a final answer. That works for one-shot requests, but it breaks closure for tasks that naturally span multiple rounds or depend on external state changes: pull requests waiting for merge, deployments waiting for checks, reviews waiting for follow-up, or any task that is not truly done after the first response.

The desired behavior is AI-native rather than workflow-hardcoded:

- heartbeat should act like the bot reviewing its unfinished work;
- manager should inspect open tasks, decide which one to advance, and choose the right tools;
- task states must describe closure semantics clearly enough that the model can self-govern;
- scenario-specific automation such as a dedicated PR tracker should not become the primary architecture.

PR follow-up is the motivating case, but the design must generalize to unfinished work as a whole.

## Goals

1. Let manager keep tasks open until their real delivery condition is satisfied.
2. Add a generic `waiting_external` state for tasks that are blocked on outside change but still need proactive follow-up.
3. Let heartbeat trigger a manager-led review of unfinished tasks without hardcoding PR-specific execution flows.
4. Give manager a generic tool for listing and updating unfinished-task state.
5. Require explicit user-facing notice before manager starts automatic follow-up actions from heartbeat.

## Non-Goals

- Building a PR-only tracker subsystem.
- Encoding fixed GitHub review-handling workflows into heartbeat.
- Replacing file-backed task persistence.
- Designing a universal planner for every future automation domain in this change.

## Design Principles

### Completion Means Closure, Not One Round Of Work

Manager must distinguish between:

- an execution round being finished; and
- the user task being truly complete.

If the task still depends on external confirmation or later follow-up, the task must stay open.

### Heartbeat Is Self-Review, Not A Passive Alarm

Heartbeat should not be limited to fixed scenario runners. It should be able to revisit unfinished work the same way a person checks their remaining TODOs.

### The System Hardcodes State Semantics, Not Scenario Workflows

Core code may define task states, closure metadata, and review scheduling semantics. Core code should not hardcode how PR review, deployment checks, or other domains must be handled step by step if manager can choose tools itself.

### Manager Must Be Able To See And Update Task State Directly

If manager is responsible for closure, it needs a direct manager-side tool to:

- inspect unfinished tasks;
- inspect one task's closure contract and references;
- change task status and follow-up metadata;
- optionally announce a follow-up action to the user before proceeding.

## Task Model Changes

### New Task Status

Add `waiting_external` to task inbox status semantics.

Meaning:

- the task is not complete;
- the bot is waiting for the outside world to change;
- heartbeat may proactively revisit the task;
- manager should only mark it `completed` when the closure condition is truly satisfied.

### Closure Metadata

Store generic follow-up metadata on the task envelope under a dedicated metadata subtree.

Recommended shape:

```python
metadata["followup"] = {
    "done_when": "PR merged on GitHub",
    "next_review_after": "2026-03-13T14:30:00+08:00",
    "refs": {
        "repo": "owner/repo",
        "pr_url": "https://github.com/.../pull/123",
        "workspace_id": "ws_...",
        "repo_root": "/app/...",
        "branch": "feature/...",
        "codex_session_id": "cs_...",
    },
    "notes": "If review requests changes, announce to user before auto-fixing.",
    "announce_before_action": True,
    "last_review_at": "",
    "last_observation": "",
    "last_action_summary": "",
    "last_announcement_at": "",
    "last_announcement_key": "",
}
```

This metadata is intentionally generic. PR follow-up is just one possible use of `refs` and `done_when`.

Core only gives semantic meaning to a very small subset:

- `done_when`
- `next_review_after`
- `announce_before_action`
- review/action audit timestamps

`refs` is otherwise an opaque manager-facing map. Core may persist and surface it, but should not build domain logic around specific keys such as `pr_url` or `codex_session_id`.

## Session-State Contract

Task inbox is the canonical closure ledger. `heartbeat_store.status.session.active_task` remains the user-facing session snapshot.

For `waiting_external`:

- task inbox status becomes `waiting_external` and remains authoritative;
- session active task also becomes `waiting_external`;
- session active task is not auto-cleared by the final response path;
- this state is non-blocking for normal chat: it is an open follow-up, not an immediate user prompt;
- future chat or heartbeat runs may reuse the snapshot as context, but the unfinished-task list still comes from task inbox.

`waiting_user` remains distinct: it is an immediate blocking question for the user. `waiting_external` is a long-lived open task that heartbeat may revisit.

## Review Scheduling And Due-Time Semantics

Heartbeat should not blindly poll every open task on every tick.

Rules:

- `next_review_after` is the earliest review time for a follow-up task;
- `task_tracker.list_open` should support due-only listing and return due tasks first;
- when heartbeat asks manager to review unfinished work, manager should normally inspect due tasks first;
- when manager observes "no change", it should update `last_review_at`, optionally `last_observation`, and push `next_review_after` forward;
- when manager takes a new action, it should update `last_action_summary` and set a fresh `next_review_after`.

This preserves the AI-native decision model while still giving runtime enough structure to avoid constant rechecking.

## Manager Tool: `task_tracker`

Expose a manager-side direct tool named `task_tracker`.

### Actions

- `list_open`: list unfinished tasks for the current user.
- `get`: inspect one task in detail.
- `update`: update status, closure metadata, progress summary, and optional user announcement.

### Announcement Ordering And Idempotency

If `update` includes `announce_text`, the tool must treat the announcement as a first-class audited side effect.

Required ordering:

1. persist the intended announcement key and timestamp attempt metadata;
2. send the proactive message;
3. persist send result fields such as `last_announcement_at` / `last_announcement_key`.

If the same `announce_key` is replayed for the same task, the tool should suppress duplicate sends.

### Why A Tool Is Required

Without a direct task-state tool, manager can only reason in prompt text while the runtime still auto-completes the task. The new tool provides a canonical way for manager to express:

- this task is still open;
- this is what completion means;
- this is when to review it again;
- this is what I am about to do.

## Orchestrator Completion Rules

The manager loop must stop auto-completing a task if manager has explicitly moved it into `waiting_external`.

Required behavior:

- final text may still be delivered to the user;
- task inbox status must remain `waiting_external`;
- session state must also remain `waiting_external` and must not be cleared as if the task were truly done;
- heartbeat should still be able to discover and revisit it later.

This is the key runtime guardrail that makes AI-native closure possible.

## Heartbeat Integration

Heartbeat itself does not need PR-specific logic.

Instead:

1. The user can add a checklist item such as "检查未完成的任务并完成他们".
2. Heartbeat runs manager with that goal.
3. Manager calls `task_tracker.list_open`.
4. Manager chooses one or more unfinished tasks to review.
5. Manager uses normal tools (`gh_cli`, `codex_session`, `git_ops`, etc.) as needed.
6. Manager updates the selected task with `task_tracker.update`.

This keeps heartbeat generic and pushes domain judgment into manager.

## Concurrency And Ownership

The system already has per-user heartbeat locking and active-task coordination. The unfinished-task review model should reuse those guardrails instead of inventing scenario-specific runners.

Rules:

- only one heartbeat review loop may run per user at a time;
- heartbeat-driven follow-up operates under the existing per-user heartbeat lock;
- if the user starts a normal chat while a task is `waiting_external`, the task stays open, but the new chat may still proceed;
- `task_tracker.update` should stamp `last_review_at` / `last_action_summary` so repeated heartbeat passes can detect that another loop already touched the task recently.

## User Notification Before Automatic Follow-Up

When manager is about to take an automatic action on an unfinished task during heartbeat, it must send a user-visible notice first.

This should be handled generically through `task_tracker.update` by allowing an `announce_text` payload that proactively pushes a short notice before the follow-up action starts.

Examples:

- "I found new review feedback on your open task and I'm starting a fix round now."
- "I checked the unfinished deployment task and I'm investigating a failed check."

No announcement is needed when heartbeat only observes that nothing changed.

## First-Scope Behavior

The first implementation should support this end-to-end path:

1. manager creates a PR;
2. manager marks the task `waiting_external` with GitHub refs and a `done_when` summary;
3. heartbeat checklist asks manager to review unfinished tasks;
4. manager lists open tasks, inspects the PR-backed one, checks GitHub state, and decides what to do;
5. if new action is needed, manager announces it, performs the action, and updates task metadata;
6. if the PR is merged, manager marks the task `completed`.

The implementation must stay generic enough that future unfinished-task domains can reuse the same state model.

## Testing Strategy

Add regression coverage for:

- `waiting_external` status persistence and listing;
- `task_tracker` update/list behavior;
- orchestrator final-response handling when the current task is `waiting_external`;
- heartbeat-driven review prompts being able to surface unfinished tasks without forcing completion;
- background announcement behavior when manager starts an automatic follow-up action.
