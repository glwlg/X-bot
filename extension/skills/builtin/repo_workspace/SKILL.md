---
api_version: v3
name: repo_workspace
description: Prepare and inspect per-session repository workspaces for manager-driven coding tasks.
triggers:
- repository workspace
- repo workspace
- clone repo
- prepare worktree
- inspect workspace
allowed_roles:
- manager
policy_groups:
- management
- coding
platform_handlers: false
tool_exports:
- name: repo_workspace
  description: Prepare, inspect, and clean per-session repository workspaces using managed worktrees.
  handler: manager.repo_workspace
  prompt_hint: 在开始代码开发前，优先用 `repo_workspace` 为仓库准备独立 worktree；准备完成后应继续调用 `codex_session` 或 `git_ops` 推进任务，不要只做只读分析就停下。查看当前开发目录状态或清理旧工作区时也用它。
  policy_groups:
  - management
  - coding
  parameters:
    type: object
    properties:
      action:
        type: string
        description: prepare | inspect | cleanup
      workspace_id:
        type: string
        description: Existing workspace id for inspect or cleanup
      repo_url:
        type: string
        description: Git repository URL used by action=prepare
      repo_path:
        type: string
        description: Existing local repository path used by action=prepare
      repo_root:
        type: string
        description: Direct workspace root override used by action=inspect
      base_branch:
        type: string
        description: Base branch for new worktree
      branch_name:
        type: string
        description: Branch name for the session worktree
      mode:
        type: string
        description: fresh_worktree | reuse_latest
      force:
        type: boolean
        description: Force cleanup when action=cleanup
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Repo Workspace

Manager 用这个技能准备隔离的仓库开发 worktree，避免直接在脏工作区里切分支或覆盖改动。

## CLI

可直接在技能目录执行：

`python scripts/execute.py --action inspect --workspace-id <workspace_id>`
