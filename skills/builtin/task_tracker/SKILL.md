---
api_version: v3
name: task_tracker
description: Manager-side unfinished-task tracker for listing open tasks, inspecting closure contracts, and updating task follow-up state.
triggers:
- unfinished task
- follow up
- waiting_external
- task tracker
- 未完成任务
- 跟进任务
allowed_roles:
- manager
policy_groups:
- management
platform_handlers: false
tool_exports:
- name: task_tracker
  description: List, inspect, and update unfinished task state for AI-native closure workflows.
  handler: manager.task_tracker
  prompt_hint: 需要查看未完成任务、把任务标成 `waiting_external`、记录 `done_when` / `next_review_after`，或在自动跟进前先通知用户时，直接调用 `task_tracker`。当任务创建了 PR、部署、工单等外部依赖但尚未真正闭环时，不要直接结束任务，先用 `task_tracker` 更新状态。
  policy_groups:
  - management
  parameters:
    type: object
    properties:
      action:
        type: string
        description: list_open | get | update
      user_id:
        type: string
        description: Target runtime user id; defaults to current user when omitted by manager handler
      task_id:
        type: string
        description: Target task inbox id; defaults to the current task for update/get when omitted
      limit:
        type: integer
        description: Max tasks returned by list_open
      due_only:
        type: boolean
        description: Only return tasks whose next_review_after is due
      event_limit:
        type: integer
        description: Max task-scoped events returned by `get`; `list_open` still only returns the latest event summary
      status:
        type: string
        description: New task status such as running, waiting_external, waiting_user, completed, failed
      result_summary:
        type: string
        description: Latest user-visible summary for the task
      done_when:
        type: string
        description: Human-readable completion condition
      next_review_after:
        type: string
        description: ISO timestamp for the next follow-up review window
      refs:
        type: object
        description: Opaque manager-facing references such as PR URL, workspace id, repo root, branch, or session id
      notes:
        type: string
        description: Persistent follow-up guidance for future manager rounds
      announce_before_action:
        type: boolean
        description: Whether future automatic follow-up should announce itself before acting
      last_observation:
        type: string
        description: Latest observation when no action was required
      last_action_summary:
        type: string
        description: Latest action the manager took for this task
      announce_text:
        type: string
        description: Optional proactive message sent to the user before an automatic follow-up action
      announce_key:
        type: string
        description: Optional dedupe key for announce_text
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: false
  network: limited
entrypoint: scripts/execute.py
---

# Task Tracker

Manager 用这个 direct tool 显式维护未完成任务状态。它不是场景专属 PR tracker，而是通用闭环工具：

- 查看当前用户仍未闭环的任务
- 读取某个任务的 follow-up 上下文
- 把任务标记成 `waiting_external`
- 记录 `done_when`、`next_review_after` 和外部引用
- 在 heartbeat 自动继续处理前，先给用户发一条简短通知
