---
api_version: v3
name: reminder
description: 简单文本提醒工具。仅用于一次性提醒；重复任务必须转给 scheduler_manager。
triggers:
- 提醒
- remind
- timer
- 闹钟
- alarm
policy_groups:
- automation
platform_handlers: false
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Reminder

这是一次性倒计时提醒。通过 `bash` 执行脚本，不要把重复调度塞进这个 skill。

## Command

- `python scripts/execute.py 10m "喝水"`
- `python scripts/execute.py 1h30m "开会"`

## Rules

- 仅支持相对时间，例如 `30s`、`10m`、`1h`、`1d`、`1h30m`。
- 如果用户要“每天/每周/每小时”执行，改用 `scheduler_manager`。
