---
api_version: v3
name: skill_manager
description: 核心技能中心。负责列出、搜索、安装、创建、修改和删除技能。
triggers:
- 搜索技能
- 修改技能
- 安装技能
- 删除技能
- 列出技能
- 创建技能
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Skill Manager

通过 `bash` 调用 CLI。创建和修改技能时，本 skill 会转走 manager 的 `software_delivery` 模板任务，不要自己临时拼接另外一套流程。

## Commands

- 列出技能：`python scripts/execute.py list`
- 搜索技能：`python scripts/execute.py search "<query>"`
- 安装技能：`python scripts/execute.py install <url_or_owner/repo>`
- 创建技能：`python scripts/execute.py create "<requirement>" [--skill-name <name>] [--backend codex|gemini-cli]`
- 修改技能：`python scripts/execute.py modify <skill_name> "<instruction>" [--backend codex|gemini-cli]`
- 删除 learned skill：`python scripts/execute.py delete <skill_name>`

## Rules

- 创建前优先 `search`，避免重复造轮子。
- 只能删除 learned skill，不能删除 builtin skill。
