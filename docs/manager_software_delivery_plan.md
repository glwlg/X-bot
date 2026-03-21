# Manager Software Delivery Plan

## Goal

Enable Manager to execute end-to-end software delivery from user intent or GitHub issue:

- Read GitHub issues and comments
- Plan development tasks and acceptance criteria
- Implement code changes with coding backend
- Validate changes with project-native test commands
- Commit, push, create PR, and comment back on issue

## Architecture

### Manager Pipeline

- `src/manager/dev/service.py`
  - Unified pipeline orchestration
  - Actions: `run`, `read_issue`, `plan`, `implement`, `validate`, `publish`, `status`, `resume`, `skill_create`, `skill_modify`
  - Persists task lifecycle and supports resume
  - Skill template actions route skill creation/modification through the same software_delivery capability surface

### GitHub Integration

- `src/manager/integrations/github_client.py`
  - Parse issue references (URL, `owner/repo#id`, numeric id with defaults)
  - Fetch issue details and comments
  - Create PR and issue comments
  - Enforce optional repo allowlist and token-based write operations

### Task State and Recovery

- `src/manager/dev/task_store.py`
  - Persistent per-task JSON records under `data/system/dev_tasks`
  - Status progression:
    - `planned`
    - `implementing`
    - `implemented`
    - `validating`
    - `validated`
    - `publishing`
    - `done`
    - `failed`

### Workspace and Execution

- `src/manager/dev/workspace.py`
  - Resolve repo path, clone/pull from repo URL, infer owner/repo from git remote
  - Prepare and switch branch for implementation

- `src/manager/dev/runtime.py`
  - Safe command execution helpers (`run_shell`, `run_exec`)
  - Coding backend execution with codex/gemini-cli support
  - Retry codex trust-check case with `--skip-git-repo-check`

### Planning, Validation, Publishing

- `src/manager/dev/planner.py`
  - Build deterministic delivery plan from requirement + issue context
  - Produce branch naming, commit message, PR title/body defaults

- `src/manager/dev/validator.py`
  - Auto-detect validation command by repo type (`uv run pytest`, `npm test`, `cargo test`)
  - Support explicit `validation_commands`

- `src/manager/dev/publisher.py`
  - Detect sensitive changed files and block publish when risky
  - `git add`, `git commit`, `git push`
  - Create PR through GitHub API when enabled

## Orchestrator Integration

- New manager tool: `software_delivery`
  - Added to `src/core/tool_registry.py`
  - Wired in `src/core/orchestrator_runtime_tools.py`
  - Tool facade in `src/core/tools/dev_tools.py`

- Policy mapping
  - `software_delivery` mapped to management group in `src/core/tool_access_store.py`
  - Available to manager runtime only; historical worker-runtime policy notes are no longer part of the current architecture

## Five Phases

### Phase 1: GitHub Read

- Implement issue parsing and fetch in `github_client`
- Return title/body/labels/comments for downstream planning

### Phase 2: Local Dev Pipeline

- Create plan and task record
- Build implementation instruction from requirement and issue context
- Execute coding backend in repo workspace

### Phase 3: Git Publish and PR

- Stage and commit repository changes
- Push branch to origin
- Create pull request and capture URL

### Phase 4: Task State and Resume

- Persist lifecycle records in task store
- Resume from partial states with `resume` action
- Keep structured events for auditability

### Phase 5: Unified Entry

- Expose all stages behind one manager tool `software_delivery`
- Support both one-shot `run` and staged actions

## Configuration

Recommended environment keys:

- `GITHUB_TOKEN`
- `GITHUB_ALLOWED_REPOS`
- `GITHUB_DEFAULT_OWNER`
- `GITHUB_DEFAULT_REPO`
- `DEV_WORKSPACE_ROOT`
- `DEV_TASKS_ROOT`
- `DEV_VALIDATION_TIMEOUT_SEC`

## Safety Defaults

- Write operations to GitHub require token
- Optional allowlist can restrict repository targets
- Sensitive file patterns block publishing
- Failures are explicit and terminal for run/publish stages

## Validation Plan

- Unit coverage for manager dev pipeline and tool delegation
- Existing core orchestration tests must remain green
- End-to-end flow should produce:
  - task id
  - status transitions
  - commit sha
  - optional PR URL
