# BUILTIN SKILLS KNOWLEDGE BASE

## OVERVIEW
`skills/builtin/` contains first-party runtime extensions with explicit `SKILL.md` contracts and executable script entrypoints.

## STRUCTURE
```text
skills/builtin/
|- <skill>/
|  |- SKILL.md              # contract, trigger, constraints
|  `- scripts/execute.py    # runtime entrypoint
`- ...
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Skill execution contract | `skills/builtin/*/SKILL.md` | authoritative behavior and restrictions |
| Runtime implementation | `skills/builtin/*/scripts/execute.py` | actual callable logic |
| Deployment path and constraints | `skills/builtin/deployment_manager/` | largest/highest-complexity built-in skill |
| Docker command gateway | `skills/builtin/docker_ops/` | strict command scope and forbidden actions |
| Subscription and scheduled behaviors | `skills/builtin/rss_subscribe/`, `skills/builtin/scheduler_manager/`, `skills/builtin/reminder/` | long-running/periodic workflows |

## CONVENTIONS
- Keep behavior constraints in `SKILL.md` aligned with script behavior; contract drift is a bug.
- Keep `execute.py` focused on one skill domain; shared primitives belong in core/services.
- Prefer explicit input validation and deterministic output schemas for skill calls.
- Preserve bilingual or mixed-language docs where existing skill contracts already use them.

## ANTI-PATTERNS
- Don't bypass skill-level restrictions declared in `SKILL.md` (forbidden actions are binding).
- Don't move orchestration policy into individual skill scripts.
- Don't introduce broad shell execution in skills that are intended to be scoped tools.
- Don't couple builtin skill scripts directly to ad-hoc paths outside repository abstractions.

## QUICK CHECKS
```bash
uv run pytest tests/core/test_extension_executor.py
uv run pytest tests/core/test_dispatch_tools.py
```
