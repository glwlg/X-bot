---
name: reminder
description: 设置定时提醒，支持 10m/1h/30s 等时间格式
triggers:
- 提醒
- remind
- timer
- 定时
- 闹钟
- alarm
---
# Reminder

提醒 Skill - 设置定时提醒

## 使用方法

**触发词**: `提醒`, `remind`, `timer`, `定时`, `闹钟`

## 参数

- **time** (`str`) (必需): 时间间隔，如 10m, 1h, 30s
- **content** (`str`) (必需): 提醒内容

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
