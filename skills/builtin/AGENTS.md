# BUILTIN SKILLS KNOWLEDGE BASE

## OVERVIEW
`skills/builtin/` contains first-party runtime extensions with explicit `SKILL.md` contracts and executable script entrypoints.

## STRUCTURE
```text
skills/builtin/
|- <skill>/
|  |- SKILL.md              # contract, trigger phrases, constraints
|  |- SKILL_SPEC.md         # optional extended schema/spec
|  `- scripts/execute.py    # runtime entrypoint
`- ...
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Skill contract and input schema | `skills/builtin/*/SKILL.md` | authoritative behavior, constraints, and trigger rules |
| Runtime implementation | `skills/builtin/*/scripts/execute.py` | actual callable logic |
| Skill lifecycle and coding workflows | `skills/builtin/skill_manager/` | skill creation/update governance and coding-mode behavior |
| Search and research stack | `skills/builtin/web_search/`, `skills/builtin/web_extractor/`, `skills/builtin/web_browser/`, `skills/builtin/deep_research/` | retrieval, extraction, browsing, long-form synthesis |
| Ops and deployment stack | `skills/builtin/deployment_manager/`, `skills/builtin/docker_ops/` | deployment orchestration and Docker command gateway |
| Automation and user-state stack | `skills/builtin/reminder/`, `skills/builtin/rss_subscribe/`, `skills/builtin/scheduler_manager/`, `skills/builtin/stock_watch/`, `skills/builtin/account_manager/` | reminders, subscriptions, scheduled jobs, watchlists, account bindings |
| Media pipeline | `skills/builtin/download_video/` | media fetch/transcode and platform-aware delivery preparation |

## CONVENTIONS
- Keep behavior constraints in `SKILL.md` aligned with script behavior; contract drift is a bug.
- Keep each `execute.py` focused on one skill domain; shared primitives belong in `src/core` or `src/services`.
- Prefer explicit input validation and deterministic output schemas for skill calls.
- Persist user/system state through core state APIs instead of ad-hoc file paths.
- Preserve bilingual or mixed-language docs where existing skill contracts already use them.

## ANTI-PATTERNS
- Don't bypass skill-level restrictions declared in `SKILL.md` (forbidden actions are binding).
- Don't move orchestration policy into individual skill scripts.
- Don't introduce broad shell execution in skills that are intended to be scoped tools.
- Don't couple builtin skill scripts directly to ad-hoc paths outside approved runtime/state boundaries.

## QUICK CHECKS
```bash
uv run pytest tests/core/test_extension_executor.py
uv run pytest tests/core/test_dispatch_tools.py
uv run pytest tests/core/test_web_search_execute.py
uv run pytest tests/core/test_rss_subscribe_execute.py
uv run pytest tests/core/test_skill_manager_coding_modes.py
```
