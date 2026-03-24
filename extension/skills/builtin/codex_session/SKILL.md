---
api_version: v3
name: codex_session
description: Manager-side coding session bridge for starting and continuing Codex development rounds in prepared workspaces.
triggers:
- codex session
- continue coding
- coding session
- 继续 codex
- ask codex
allowed_roles:
- manager
policy_groups:
- management
- coding
platform_handlers: false
tool_exports:
- name: codex_session
  description: Start, continue, inspect, or cancel a Codex coding session bound to a prepared workspace.
  handler: manager.codex_session
  prompt_hint: 需要让 Codex 在某个已准备好的工作区里开发代码时，直接调用 `codex_session`。如果用户已经给了足够的创意或风格方向，就直接开始实现，不要先反问风格偏好；只有真正缺少关键约束时才提问。如果 Codex 提出澄清问题，再拿用户回复调用 `continue`。
  policy_groups:
  - management
  - coding
  parameters:
    type: object
    properties:
      action:
        type: string
        description: start | continue | status | cancel
      session_id:
        type: string
        description: Existing coding session id
      workspace_id:
        type: string
        description: Prepared workspace id for action=start
      cwd:
        type: string
        description: Direct workspace path override
      instruction:
        type: string
        description: Coding instruction or continuation context
      user_reply:
        type: string
        description: User answer used when action=continue
      backend:
        type: string
        description: Coding backend, defaults to codex
      timeout_sec:
        type: integer
        description: Timeout for each coding round
      source:
        type: string
        description: Optional session source tag for internal manager workflows
      skill_name:
        type: string
        description: Optional skill name tag for local skill workflows
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Codex Session

Manager 用这个技能直接驱动 Codex 开发回合。它支持在用户确认后继续同一逻辑会话，而不是把实现过程硬编码成固定流水线。

## CLI

可直接在技能目录执行：

`python scripts/execute.py --action status --session-id <session_id>`
