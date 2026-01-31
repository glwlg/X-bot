---
name: reminder
description: |
  简单的文本提醒工具。仅用于“X分钟后叫我”、“提醒我喝水”等简单场景。
  
  **❌ 禁止用于自动化任务**:
  - 如果用户要求“定时运行某技能”（如每天查天气、每小时发推），**绝对不要**使用此技能。
  - 请使用 `skill_manager` 的 `modify` 功能配置 `crontab`。

triggers:
- 提醒
- remind
- timer
- 闹钟
- alarm
---
# Reminder

文本提醒 Skill。

## 使用方法

**触发词**: `提醒`, `remind`, `timer`, `定时`, `闹钟`

## 参数

- **time** (`str`) (必需): 时间间隔，如 10m, 1h, 30s
- **content** (`str`) (必需): 提醒内容

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
