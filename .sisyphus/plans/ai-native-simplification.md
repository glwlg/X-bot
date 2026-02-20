# AI-Native Simplification: Canonical MD State Protocol + Layer Collapse

## TL;DR

> **Quick Summary**: Keep file-backed state (Markdown) but make it *LLM-native*: one strict, machine-editable YAML payload per business state file, with fixed markers; then collapse redundant storage layers so the bot has fewer indirections and fewer files.
>
> **Deliverables**:
> - Canonical business-state Markdown protocol (BEGIN/END markers + single fenced `yaml` payload)
> - Compatibility reader + strict writer + corruption/backup policy for state files
> - Migration/normalization utility to upgrade existing `data/**` state files
> - Repositories layer collapsed (inline `src/repositories/*` into `src/core/state_store.py` and remove `src/repositories/`)
> - Tests (after) covering protocol + per-domain state behaviors
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Protocol utilities -> port 1-2 state domains -> port remaining domains -> remove `src/repositories/` -> full pytest

---

## Context

### Original Request
User wants AI-native design: private bot, no high concurrency focus, SQLite is not an automatic upgrade; current code feels over-encapsulated. Keep Markdown state files (agent-friendly), but make agents read/write them more easily, and delete unnecessary code.

### Interview Summary
- Keep Core Manager + Worker Fleet architecture.
- Simplification aggressiveness: medium-aggressive; small behavior/data-format changes acceptable.
- **State preference**: keep multiple Markdown state files (no business-state JSON pivot).
- **Protocol decision**: adopt strict canonical payload protocol for business state Markdown files:
  - fixed header + fixed BEGIN/END markers
  - exactly one fenced `yaml` block as canonical payload
  - agent edits payload only (avoid whole-file rewrites)
- **Scope (default)**:
  - IN: user business state MD + system repositories MD
  - OUT (initially): chat transcripts, memory files (MEMORY.md/daily memory), skills SKILL.md frontmatter, heartbeat specs/status
- **Test strategy**: tests-after (run `uv run pytest` as gate).

### Evidence Anchors
- Thin facade: `src/core/state_store.py` is pure re-export of `src/repositories/*`.
- Current state-file format: `src/repositories/base.py` reads/writes Markdown wrapper with fenced YAML payload.
- Similar marker pattern exists elsewhere: `src/worker_runtime/task_file_store.py` uses BEGIN/END markers for a fenced payload.
- Known handler workaround indicating storage mismatch: `src/handlers/ai_handlers.py:672`.

### Metis Review (gaps addressed)
Metis flagged the need to explicitly decide:
- Legacy format compatibility window
- Marker semantics and parser strictness
- Parse error / corruption failure mode
- Scope creep boundaries (skills frontmatter, heartbeat store, scheduler/platform coupling)

This plan sets explicit defaults (see **Guardrails** and **Defaults Applied**) and includes tests for legacy fixtures.

---

## Work Objectives

### Core Objective
Make business state files easy and safe for LLMs to read/edit (short path, predictable structure) while removing redundant storage abstractions.

### Definition of Done
- [x] Canonical protocol exists and is enforced on writes for all scoped business state files.
- [x] Reads remain compatible with existing legacy files in the scoped set.
- [x] Migration tool upgrades existing files without losing data (with backups).
- [x] `src/repositories/` removed; `src/core/state_store.py` remains the single app-facing storage surface.
- [x] `uv run pytest` passes.

### Must Have
- Strict, single canonical YAML payload region per business state `.md` file.
- Agent edits are limited to the payload region (BEGIN/END markers).
- Atomic writes + backup-on-risk (avoid silent data loss).

### Must NOT Have (Guardrails)
- Do not apply the new business-state protocol to:
  - `skills/**/SKILL.md` (frontmatter parsed by `src/core/skill_loader.py`)
  - Heartbeat store/spec (`src/core/heartbeat_store.py`) and runtime heartbeat status files
  - Memory stores (`src/core/markdown_memory_store.py`, `src/core/kernel_memory.py`)
  - Chat transcripts under `data/users/<uid>/chat/**`
- Do not opportunistically refactor orchestrator/tool routing/platform adapters/sandbox execution while doing state protocol work.
- Do not change on-disk paths for existing business state files (only normalize file *content* format).

---

## Verification Strategy (MANDATORY)

> ZERO HUMAN INTERVENTION - all verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (`pytest`)
- **Automated tests**: YES (tests-after)
- **Framework**: `pytest` via `uv run pytest`

### QA Policy
Every task includes:
- at least one deterministic, runnable command-based scenario
- evidence captured to `.sisyphus/evidence/`

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Foundation: protocol + safety + tests harness)
1. Canonical protocol spec + parse/write utilities
2. Migration/normalization utility (dry-run + apply)
3. Protocol test suite (fixtures for legacy + canonical)
4. Update agent-facing instructions (prompts/docs) to "edit payload only"

Wave 2 (Port business state domains in parallel)
5. Port settings state (translation mode)
6. Port RSS subscriptions state
7. Port stock watchlist state
8. Port reminders state
9. Port scheduled tasks state
10. Port allowed users state (system)
11. Port system caches + counters primitives

Wave 3 (Layer collapse + cleanup)
12. Port remaining repo modules (chat/account) + remove `src/repositories/`
13. Regression verification + migration validation pass

### Dependency Matrix
| Task | Depends On | Blocks | Wave |
|------|------------|--------|------|
| 1 | - | 2-13 | 1 |
| 2 | 1 | 13 | 1 |
| 3 | 1 | 5-13 | 1 |
| 4 | 1 | 13 | 1 |
| 5-11 | 1,3 | 12,13 | 2 |
| 12 | 5-11 | 13 | 3 |
| 13 | 2,4,12 | - | 3 |

---

## Defaults Applied (override anytime)

- **Legacy compatibility**: Reads accept legacy formats indefinitely for scoped business state files.
  - Legacy formats include: frontmatter (`---`), fenced `yaml` without markers, and "whole-file YAML".
- **Writer strictness**: All writes output canonical markers + exactly one fenced `yaml` block (single source of truth).
- **Corruption policy**: On parse failure of an existing file, do not silently overwrite without preserving original.
  - Default: create a timestamped backup before writing canonical output; log a warning.
- **Schema**: Each canonical payload includes `version: 1` at top-level.
- **Single-process assumption**: no cross-process locking work included.

---

## TODOs

- [x] 1. Define canonical business-state MD protocol + utilities

  **What to do**:
  - Define markers (e.g., `<!-- XBOT_STATE_BEGIN -->` / `<!-- XBOT_STATE_END -->`) and a canonical file template.
  - Implement a single shared reader/writer API for scoped business state `.md` files:
    - tolerant read (legacy supported)
    - strict write (canonical markers + single fenced `yaml`)
    - backup-on-risk (parse failure)
  - Ensure writer preserves stable key ordering (`sort_keys=False`) and UTF-8.

  **Must NOT do**:
  - Do not alter parsing rules for `skills/**/SKILL.md` or heartbeat specs.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: contract design + low-level file-format + safety behavior.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 2-13
  - **Blocked By**: None

  **References**:
  - `src/repositories/base.py` - current Markdown+YAML read/write primitives and legacy extraction behavior.
  - `src/worker_runtime/task_file_store.py` - example of BEGIN/END marker pattern around a fenced payload.

  **Acceptance Criteria**:
  - [x] New protocol markers are used on every write for scoped business state files.
  - [x] Reader can parse legacy state files that previously worked.
  - [x] Writer produces exactly one fenced `yaml` block and includes `version: 1`.

  **QA Scenarios**:
  ```
  Scenario: Canonical write produces single YAML payload
    Tool: Bash
    Preconditions: DATA_DIR is a temp directory
    Steps:
      1. Run a minimal script that writes a settings/subscriptions state file via the new API
      2. Read the file text and assert it contains BEGIN and END markers
      3. Assert it contains exactly one "```yaml" fence
    Expected Result: markers present; one fenced yaml block; payload contains version: 1
    Evidence: .sisyphus/evidence/task-1-canonical-write.txt

  Scenario: Legacy read does not crash
    Tool: Bash
    Preconditions: Create a legacy-format file (frontmatter or no markers)
    Steps:
      1. Run reader on legacy file
      2. Assert returned data matches expected fields
    Expected Result: reader returns correct structure; no overwrite happens
    Evidence: .sisyphus/evidence/task-1-legacy-read.txt
  ```

- [x] 2. Add migration/normalization utility for business state files

  **What to do**:
  - Implement a tool/command that scans scoped files under `DATA_DIR` and:
    - `--dry-run`: reports which files are legacy vs canonical
    - `--apply`: rewrites legacy files into canonical format using the writer
    - creates backups for rewritten files
  - Ensure it only touches IN-scope file paths.

  **Must NOT do**:
  - Do not modify chat transcripts or memory files.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: small CLI/tooling addition once protocol API exists.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 13
  - **Blocked By**: 1

  **References**:
  - `src/core/config.py` - `DATA_DIR` handling.
  - `src/core/prompts.py` - authoritative list of business state paths.

  **Acceptance Criteria**:
  - [x] Dry-run produces a deterministic report.
  - [x] Apply upgrades a sample user directory without errors.
  - [x] Backups created for any rewritten file.

  **QA Scenarios**:
  ```
  Scenario: Dry-run reports legacy files
    Tool: Bash
    Steps:
      1. Set DATA_DIR to a temp dir
      2. Create a few legacy-format state files in scoped locations
      3. Run migration in dry-run
      4. Assert report lists the files and states
    Expected Result: files detected; no file content changed
    Evidence: .sisyphus/evidence/task-2-dry-run.txt

  Scenario: Apply upgrades and keeps backups
    Tool: Bash
    Steps:
      1. Run migration with --apply
      2. Assert canonical markers now exist
      3. Assert backup files exist
    Expected Result: upgraded files canonical; backups present
    Evidence: .sisyphus/evidence/task-2-apply.txt
  ```

- [x] 3. Add tests for protocol + legacy fixtures (tests-after)

  **What to do**:
  - Add/extend pytest coverage to lock down:
    - canonical writer output shape
    - legacy parser compatibility
    - backup-on-risk behavior
  - Add per-domain roundtrip tests for each state file type in scope.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: avoiding regressions during refactor and deletion.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 5-13
  - **Blocked By**: 1

  **References**:
  - `tests/test_repositories.py` - existing repository tests pattern.
  - `tests/conftest.py` - `DATA_DIR` monkeypatch pattern.

  **Acceptance Criteria**:
  - [x] New tests added and passing.
  - [x] `uv run pytest` passes.

  **QA Scenarios**:
  ```
  Scenario: Run targeted repository tests
    Tool: Bash
    Steps:
      1. uv run pytest tests/test_repositories.py
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-3-pytest.txt
  ```

- [x] 4. Update agent-facing instructions to enforce payload-only edits

  **What to do**:
  - Update `src/core/prompts.py` guidance for file operations:
    - emphasize editing only within state payload markers
    - list scoped business state files and forbid editing excluded files in this migration
  - (Optional) add a short "State File Protocol" note to `DEVELOPMENT.md`.

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 13
  - **Blocked By**: 1

  **References**:
  - `src/core/prompts.py` - `MEMORY_MANAGEMENT_GUIDE` already lists state file paths.

  **Acceptance Criteria**:
  - [x] Prompt text clearly instructs agents to edit only payload regions.
  - [x] No path guidance conflicts with actual repository paths.

  **QA Scenarios**:
  ```
  Scenario: Prompt composes without errors
    Tool: Bash
    Steps:
      1. uv run python -c "from core.prompts import MEMORY_MANAGEMENT_GUIDE; print(MEMORY_MANAGEMENT_GUIDE[:200])"
    Expected Result: prints guide prefix
    Evidence: .sisyphus/evidence/task-4-prompts.txt
  ```

- [x] 5. Port settings state into `src/core/state_store.py` (canonical MD protocol)

  **What to do**:
  - Move settings state logic from `src/repositories/user_settings_repo.py` into `src/core/state_store.py`.
  - Switch settings persistence to the canonical state-file writer (markers + single fenced `yaml`).
  - Keep legacy read compatibility for existing `data/users/<uid>/settings.md` files.
  - Delete `src/repositories/user_settings_repo.py` once `core/state_store.py` owns the implementation.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 12,13
  - **Blocked By**: 1,3

  **References**:
  - `src/repositories/user_settings_repo.py` - source logic to port.
  - `src/core/state_store.py` - app-facing API for `get_user_settings` / `set_translation_mode`.

  **Acceptance Criteria**:
  - [x] Settings write outputs canonical format.
  - [x] Existing settings files still parse.

  **QA Scenarios**:
  ```
  Scenario: Settings roundtrip in temp DATA_DIR
    Tool: Bash
    Steps:
      1. DATA_DIR=$(mktemp -d) uv run python -c "import asyncio, os; os.environ['DATA_DIR']=os.environ['DATA_DIR']; from core.state_store import set_translation_mode, get_user_settings; async def main(): await set_translation_mode('u1', True); s=await get_user_settings('u1'); print(int(bool(s.get('auto_translate')))); asyncio.run(main())"
      2. Find and print settings file content; assert markers + one yaml fence
    Expected Result: prints 1; file is canonical
    Evidence: .sisyphus/evidence/task-5-settings.txt
  ```

- [x] 6. Port RSS subscriptions state into `src/core/state_store.py` (canonical MD protocol)

  **What to do**:
  - Move subscription state logic from `src/repositories/subscription_repo.py` into `src/core/state_store.py`.
  - Ensure writes produce canonical markers + single YAML payload.
  - Preserve legacy read compatibility for existing `data/users/<uid>/rss/subscriptions.md`.
  - Delete `src/repositories/subscription_repo.py` after port.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 12,13
  - **Blocked By**: 1,3

  **References**:
  - `src/repositories/subscription_repo.py` - source logic to port.
  - `src/core/state_store.py`

  **Acceptance Criteria**:
  - [x] Add/list/remove subscriptions still works.
  - [x] File remains canonical.

  **QA Scenarios**:
  ```
  Scenario: Subscription add/list roundtrip
    Tool: Bash
    Steps:
      1. DATA_DIR=$(mktemp -d) uv run python -c "import asyncio, os; from core.state_store import add_subscription, get_user_subscriptions; async def main(): await add_subscription('u1','https://example.com/feed','Example Feed'); subs=await get_user_subscriptions('u1'); print(len(subs)); asyncio.run(main())"
    Expected Result: prints 1
    Evidence: .sisyphus/evidence/task-6-subs.txt
  ```

- [x] 7. Port stock watchlist state into `src/core/state_store.py` (canonical MD protocol)

  **What to do**:
  - Move watchlist logic from `src/repositories/watchlist_repo.py` into `src/core/state_store.py`.
  - Ensure writes canonicalize `data/users/<uid>/stock/watchlist.md`.
  - Preserve legacy read compatibility.
  - Delete `src/repositories/watchlist_repo.py` after port.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 12,13
  - **Blocked By**: 1,3

  **References**:
  - `src/repositories/watchlist_repo.py` - source logic to port.
  - `src/core/state_store.py`

  **Acceptance Criteria**:
  - [x] Add/remove/list still works.
  - [x] File canonical.

  **QA Scenarios**:
  ```
  Scenario: Watchlist add/list roundtrip
    Tool: Bash
    Steps:
      1. DATA_DIR=$(mktemp -d) uv run python -c "import asyncio, os; from core.state_store import add_watchlist_stock, get_user_watchlist; async def main(): await add_watchlist_stock('u1','AAPL','Apple Inc'); wl=await get_user_watchlist('u1'); print(len(wl)); asyncio.run(main())"
    Expected Result: prints 1
    Evidence: .sisyphus/evidence/task-7-watchlist.txt
  ```

- [x] 8. Port reminders state into `src/core/state_store.py` (canonical MD protocol)

  **What to do**:
  - Move reminder logic from `src/repositories/reminder_repo.py` into `src/core/state_store.py`.
  - Ensure writes canonicalize `data/users/<uid>/automation/reminders.md`.
  - Preserve legacy read compatibility.
  - Delete `src/repositories/reminder_repo.py` after port.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 12,13
  - **Blocked By**: 1,3

  **References**:
  - `src/repositories/reminder_repo.py` - source logic to port.
  - `src/core/state_store.py`

  **Acceptance Criteria**:
  - [x] Add/delete/list reminders still works.
  - [x] File canonical.

  **QA Scenarios**:
  ```
  Scenario: Reminder add/list/delete
    Tool: Bash
    Steps:
      1. DATA_DIR=$(mktemp -d) uv run python -c "import asyncio; from core.state_store import add_reminder, get_pending_reminders, delete_reminder; async def main(): rid=await add_reminder('u1', chat_id=1, message='hi', trigger_time='2099-01-01T00:00:00+00:00'); rows=await get_pending_reminders(user_id='u1'); print(int(len(rows)>0)); await delete_reminder(rid, user_id='u1'); rows2=await get_pending_reminders(user_id='u1'); print(int(len(rows2)==0)); asyncio.run(main())"
    Expected Result: prints 1 then 1
    Evidence: .sisyphus/evidence/task-8-reminders.txt
  ```

- [x] 9. Port scheduled tasks state into `src/core/state_store.py` (canonical MD protocol)

  **What to do**:
  - Move scheduled task logic from `src/repositories/task_repo.py` into `src/core/state_store.py`.
  - Ensure writes canonicalize `data/users/<uid>/automation/scheduled_tasks.md`.
  - Preserve legacy read compatibility.
  - Delete `src/repositories/task_repo.py` after port.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 12,13
  - **Blocked By**: 1,3

  **References**:
  - `src/repositories/task_repo.py` - source logic to port.
  - `src/core/state_store.py`

  **Acceptance Criteria**:
  - [x] Add/update/delete scheduled task works.
  - [x] File canonical.

  **QA Scenarios**:
  ```
  Scenario: Scheduled task add/list
    Tool: Bash
    Steps:
      1. DATA_DIR=$(mktemp -d) uv run python -c "import asyncio; from core.state_store import add_scheduled_task, get_all_active_tasks; async def main(): tid=await add_scheduled_task('0 0 * * *', 'echo hi', user_id='u1'); tasks=await get_all_active_tasks(user_id='u1'); print(int(any(int(t.get('id') or 0)==int(tid) for t in tasks))); asyncio.run(main())"
    Expected Result: prints 1
    Evidence: .sisyphus/evidence/task-9-scheduled.txt
  ```

- [x] 10. Port system allowed users state into `src/core/state_store.py` (canonical MD protocol)

  **What to do**:
  - Move allowed-users logic from `src/repositories/allowed_users_repo.py` into `src/core/state_store.py`.
  - Ensure writes canonicalize `data/system/repositories/allowed_users.md`.
  - Preserve legacy read compatibility.
  - Delete `src/repositories/allowed_users_repo.py` after port.

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 12,13
  - **Blocked By**: 1,3

  **References**:
  - `src/repositories/allowed_users_repo.py` - source logic to port.
  - `src/core/state_store.py`
  - `src/core/config.py` - allowed-user checks.

  **Acceptance Criteria**:
  - [x] Add/remove/check allowed users works.
  - [x] File canonical.

  **QA Scenarios**:
  ```
  Scenario: Allowed users add/check/remove
    Tool: Bash
    Steps:
      1. DATA_DIR=$(mktemp -d) uv run python -c "import asyncio, os; os.environ['DATA_DIR']=os.environ['DATA_DIR']; from core.state_store import add_allowed_user, remove_allowed_user, check_user_allowed_in_db; async def main(): await add_allowed_user('u1'); print(int(await check_user_allowed_in_db('u1'))); await remove_allowed_user('u1'); print(int(await check_user_allowed_in_db('u1'))==0); asyncio.run(main())"
    Expected Result: prints 1 then 1
    Evidence: .sisyphus/evidence/task-10-allowed.txt
  ```

- [x] 11. Port system caches + counters primitives out of `src/repositories/`

  **What to do**:
  - Move cache logic from `src/repositories/cache_repo.py` into core (either `src/core/state_store.py` or a dedicated core module).
  - Move/replace primitives from `src/repositories/base.py` into core (recommended: `src/core/state_file.py` for format + `src/core/state_paths.py` for pathing).
  - Ensure counters file `data/system/repositories/id_counters.md` written by `next_id()` is canonical.
  - Ensure video cache storage is canonical.
  - Delete `src/repositories/cache_repo.py` and `src/repositories/base.py` after successful port.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: touches base primitives used by many state domains.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 12,13
  - **Blocked By**: 1,3

  **References**:
  - `src/repositories/cache_repo.py` - source logic to port.
  - `src/repositories/base.py` - source primitives to port.

  **Acceptance Criteria**:
  - [x] Video cache read/write works.
  - [x] next_id works and writes canonical counters.

  **QA Scenarios**:
  ```
  Scenario: Cache read/write and counter increment
    Tool: Bash
    Steps:
      1. DATA_DIR=$(mktemp -d) uv run python -c "import asyncio, os; os.environ['DATA_DIR']=os.environ['DATA_DIR']; from core.state_store import save_video_cache, get_video_cache, next_id; async def main(): await save_video_cache('k','v'); print(await get_video_cache('k')); a=await next_id('x'); b=await next_id('x'); print(int(b==a+1)); asyncio.run(main())"
    Expected Result: prints v; then 1
    Evidence: .sisyphus/evidence/task-11-cache-counters.txt
  ```

- [x] 12. Port remaining repo modules (chat/account) + remove `src/repositories/`

  **What to do**:
  - Port the remaining repository modules not covered by tasks 5-11 (expected: `chat_repo.py`, `account_repo.py`, and any leftovers) into core.
    - Preserve on-disk format and behavior for chat transcripts and accounts (no protocol changes).
  - Ensure `src/core/state_store.py` remains the app-facing API surface.
  - Update imports so nothing references `repositories.*`.
  - Delete `src/repositories/` directory.

  **Must NOT do**:
  - Do not change the public function names used across the codebase (avoid widespread churn).

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: multi-step refactor + deletions + maintaining API stability.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: 13
  - **Blocked By**: 5-11

  **References**:
  - `src/core/state_store.py` - current single import surface.
  - `src/repositories/*.py` - remaining sources to port.

  **Acceptance Criteria**:
  - [x] `rg "from repositories" src` returns no matches.
  - [x] `uv run pytest` passes.
  - [x] No behavioral regressions for scoped state domains.

  **QA Scenarios**:
  ```
  Scenario: Repositories directory removed and imports cleaned
    Tool: Bash
    Steps:
      1. rg "from repositories" src || true
      2. uv run pytest
    Expected Result: no import matches; tests pass
    Evidence: .sisyphus/evidence/task-12-cleanup.txt
  ```

- [x] 13. Final verification + migration validation pass

  **What to do**:
  - Run migration utility against a representative temp DATA_DIR with mixed legacy/canonical files.
  - Run full test suite.
  - Optionally run a small smoke path through handlers that touch settings/subscriptions/watchlist.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocked By**: 2,4,12

  **References**:
  - `src/main.py` - startup path (only for smoke checks).
  - `tests/integration/test_manager_worker_acceptance.py` - integration patterns.

  **Acceptance Criteria**:
  - [x] `uv run pytest` passes.
  - [x] Migration apply produces canonical state files in scope.
  - [x] No out-of-scope files modified.

  **QA Scenarios**:
  ```
  Scenario: Full pytest gate
    Tool: Bash
    Steps:
      1. uv run pytest
    Expected Result: PASS
    Evidence: .sisyphus/evidence/task-13-pytest.txt
  ```

---

## Commit Strategy

Suggested atomic commits (executor may adjust):
- Commit A: protocol utilities + tests scaffolding
- Commit B: migration utility + fixtures
- Commit C: per-domain canonicalization (one domain per commit)
- Commit D: collapse repositories + delete directory + final pytest

---

## Success Criteria

### Verification Commands
```bash
uv run pytest
```

### Final Checklist
- [x] Canonical markers + single YAML payload present for all scoped business state files
- [x] Legacy reads still work for pre-existing files
- [x] Backups created when rewriting risky files
- [x] No changes to excluded domains (chat, memory, skills SKILL.md, heartbeat)
- [x] `src/repositories/` removed; `src/core/state_store.py` remains the app-facing API
