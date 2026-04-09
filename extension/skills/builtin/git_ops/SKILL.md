---
api_version: v3
name: git_ops
description: Ikaros-side Git operations for checking status, diffing, committing, and pushing development workspaces.
triggers:
- git ops
- git status
- git diff
- git commit
- git push
- push branch
allowed_roles:
- ikaros
policy_groups:
- management
- coding
platform_handlers: false
tool_exports:
- name: git_ops
  description: Inspect, diff, commit, and push prepared repository workspaces with fork-aware push fallback.
  prompt_hint: 在工作区里查看代码变更、提交 commit、push 分支时优先直接用 `git_ops`；不要再用原始 `bash` 手工串联 `git status/add/commit/push`。需要开 PR 时再结合 `gh_cli`。
  policy_groups:
  - management
  - coding
  parameters:
    type: object
    properties:
      action:
        type: string
        description: status | diff | branches | commit | push
      workspace_id:
        type: string
        description: Prepared workspace id
      repo_root:
        type: string
        description: Direct repository root override
      mode:
        type: string
        description: working | staged | base, used when action=diff
      base_branch:
        type: string
        description: Base branch for diff/status/push
      message:
        type: string
        description: Commit message used when action=commit
      strategy:
        type: string
        description: auto | origin | fork, used when action=push
      branch_name:
        type: string
        description: Branch override used when action=push
      owner:
        type: string
        description: Upstream repository owner used for fork fallback
      repo:
        type: string
        description: Upstream repository name used for fork fallback
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Git Ops

Ikaros 用这个技能直接处理工作区内的 git 读写操作。它默认对 fork 场景友好，适合作为 `repo_workspace` 和 `gh_cli` 之间的代码提交桥梁。

## CLI

可直接在技能目录执行：

`python scripts/execute.py --action status --workspace-id <workspace_id>`
