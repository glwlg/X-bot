---
api_version: v3
name: coding_session
description: Ikaros-side coding session skill for starting and continuing coding rounds in prepared workspaces.
triggers:
- coding session
- continue coding
- 继续 coding
- ask coding agent
allowed_roles:
- ikaros
policy_groups:
- management
- coding
platform_handlers: false
tool_exports:
- name: coding_session
  description: Start, continue, inspect, or cancel a coding session bound to a prepared workspace.
  prompt_hint: 需要在某个已准备好的工作区里直接开发代码时，调用 `coding_session`。如果用户已经给了足够的创意或风格方向，就直接开始实现，不要先反问风格偏好；只有真正缺少关键约束时才提问。如果编码代理提出澄清问题，再拿用户回复调用 `continue`。
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
        description: Coding backend, for example codex, gemini-cli, or opencode; defaults to codex
      transport:
        type: string
        description: Optional execution transport, for example cli or acp
      timeout_sec:
        type: integer
        description: Timeout for each coding round
      source:
        type: string
        description: Optional session source tag for internal ikaros workflows
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

# Coding Session

Ikaros 用这个技能直接驱动编码回合。它支持在用户确认后继续同一逻辑会话，而不是把实现过程硬编码成固定流水线。

## CLI

可直接在技能目录执行：

`python scripts/execute.py --action status --session-id <session_id>`
