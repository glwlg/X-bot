---
api_version: v3
name: worker_management
description: "**Worker 调度管理**。列出 Worker、派发执行任务、查询执行状态。"
triggers:
- worker
- 调度 worker
- 派发任务
- worker 状态
- list_workers
- dispatch_worker
- worker_status
allowed_roles:
- manager
policy_groups:
- management
platform_handlers: false
tool_exports:
- name: list_workers
  description: List worker instances and their capabilities or status.
  handler: manager.worker_management.list
  prompt_hint: 派发前先用 `list_workers` 看当前可用 Worker、能力和负载。
  policy_groups:
  - management
  parameters:
    type: object
    properties: {}
- name: dispatch_worker
  description: Dispatch a concrete execution task to a worker.
  handler: manager.worker_management.dispatch
  prompt_hint: 需要真实执行命令、长任务或操作型技能时，用 `dispatch_worker` 派给 Worker，不要自己代执行。
  policy_groups:
  - management
  parameters:
    type: object
    properties:
      instruction:
        type: string
        description: Task instruction for worker execution
      worker_id:
        type: string
        description: Optional target worker id, omit to auto-select
      backend:
        type: string
        description: Optional backend override
      priority:
        type: integer
        description: Optional dispatch priority
      metadata:
        type: object
        description: Optional metadata for traceability
    required:
    - instruction
- name: worker_status
  description: Query recent worker execution status and task history.
  handler: manager.worker_management.status
  prompt_hint: 已经派发过的异步任务，优先用 `worker_status` 查进度，不要重复 dispatch。
  policy_groups:
  - management
  parameters:
    type: object
    properties:
      worker_id:
        type: string
        description: Optional worker id
      limit:
        type: integer
        description: Recent task limit
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Worker Management (Worker 调度 SOP)

这是 Manager 专用技能。入口脚本是 `scripts/execute.py`，通过 `bash` 调用，不要自行伪造内部函数调用。

## 使用方式

```bash
cd skills/builtin/worker_management
python scripts/execute.py <subcommand> [options]
```

## 支持的子命令

- `list`
  获取当前 Worker 列表、状态与能力。目标 Worker 不明确时先调用它。
- `dispatch <instruction> [--worker-id <id>] [--backend <name>] [--metadata '{"k":"v"}']`
  派发一个需要 Worker 执行的具体任务。
- `status [--worker-id <id>] [--limit <n>]`
  查询最近任务状态；排查异步执行进度时优先用它，而不是重复派发。

## 推荐 SOP

1. 不确定该派给谁时，先调用 `python scripts/execute.py list`。
2. 选择 Worker 时，优先看 `status`、`capabilities`、`summary`。
3. 派发时必须把 `instruction` 写完整，至少包含：
   - 目标
   - 输入材料或上下文
   - 约束条件
   - 预期交付物
4. 已经派发过的异步任务，先调用 `worker_status` 看进度，不要盲目重复 `dispatch_worker`。

## 何时使用

- 需要真实执行命令、跑长任务、访问 Worker 侧权限时。
- 需要把操作类技能交给 Worker 执行时，例如下载、运维、行情查询等。

## 示例

```bash
cd skills/builtin/worker_management
python scripts/execute.py list
python scripts/execute.py dispatch "进入 download_video 技能目录，下载给定 URL 的音频并回报 saved_path。" --worker-id worker-main
python scripts/execute.py status --worker-id worker-main --limit 5
```

## 禁止事项

- 不要把模糊的一句话直接塞给 `dispatch_worker`。
- 不要在没有状态确认的情况下连续重复派发同一任务。
