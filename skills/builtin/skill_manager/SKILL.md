---
name: skill_manager
description: 技能管理器。搜索技能市场、安装新技能、列出已安装技能、删除技能、检查更新。当用户想要管理技能、扩展能力、或询问有哪些技能时使用。
triggers:
- search_skill
- install_skill
- delete_skill
- list_skills
- check_updates
- update_skills
- modify_skill
- approve_skill
- reject_skill
---
# Skill Manager

技能管理器 - 统一的技能管理入口
支持搜索、安装、删除、更新、列出技能、**修改、审核**

## 使用方法

**触发词**: `search_skill`, `install_skill`, `delete_skill`, `list_skills`, `check_updates`, `approve_skill`

## 参数

- **action**: Action: 'search', 'install', 'delete', 'list', 'check_updates', 'update', 'modify', 'approve', 'reject'
- **query**: 搜索关键词 (for 'search')
- **skill_name**: 技能名称 (for 'install', 'delete', 'modify', 'approve', 'reject')
- **repo_name**: 仓库地址 owner/repo (for 'install')
- **instruction**: 修改指令 (for 'modify')

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
