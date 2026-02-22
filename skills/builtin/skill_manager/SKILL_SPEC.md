# X-Bot Skill Specification

This file is the coding contract for creating or modifying skills under `skills/learned/*`.

## 1. Directory Scope

- Only edit files inside the current target skill directory.
- Never edit `src/`, `skills/builtin/`, or other skill directories.

## 2. Required File Layout

- `SKILL.md` (required)
- `scripts/execute.py` (required for executable skills)

## 3. `SKILL.md` Frontmatter (required fields)

```yaml
---
api_version: v3
name: your_skill_name
description: short description
triggers:
  - trigger 1
  - trigger 2
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: false
  network: limited
entrypoint: scripts/execute.py
---
```

Notes:
- `name` must be snake_case.
- `description` should be concise and behavior-focused.
- `triggers` should include practical natural-language intents.

## 4. `scripts/execute.py` contract

- Function signature must be:
  - `async def execute(ctx, params: dict, runtime=None) -> dict`
- Return a dict with `text` and optional `ui`.
- Returned `text` should keep the `🔇🔇🔇` prefix convention.
- Handle invalid params with clear error text.

## 5. Change Rules (for modify tasks)

- Keep existing skill identity (`name`) unless explicitly requested.
- Keep backward-compatible params whenever possible.
- Do not remove working behavior unless requirement explicitly replaces it.

## 6. Completion Marker

At the end of CLI output, print:

`CREATED_SKILL=<skill_name>`

For modify tasks, use the existing skill name.
