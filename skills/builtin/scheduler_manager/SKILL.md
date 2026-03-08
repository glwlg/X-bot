---
api_version: v3
name: scheduler_manager
description: 管理周期性定时任务（Cron），支持添加、查看、删除任务。
triggers:
- schedule
- cron
- task
- 定时任务
- 周期任务
- 自动运行
- 周期执行
- 每天
- 每小时
policy_groups:
- automation
platform_handlers: true
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Scheduler Manager

这是周期任务调度 skill。通过 `bash` 执行 CLI，不要再描述旧的“内置动作”。

## Commands

- 添加任务：`python scripts/execute.py add --crontab "0 8 * * *" --instruction "查询北京天气" --push true`
- 列出任务：`python scripts/execute.py list`
- 删除任务：`python scripts/execute.py delete <task_id>`

## Rules

- 周期性或自动运行需求都走这里，不走 `reminder`。
- `instruction` 尽量保留用户原始意图，不要过度总结。
