---
name: notebooklm
description: Google NotebookLM 自动化工具。管理笔记本、添加来源、提问、生成播客/视频等。
triggers:
- notebooklm
- notebook
- podcast
- 播客
---
# Notebooklm

NotebookLM Skill - Google NotebookLM 自动化工具

基于 notebooklm-py CLI 实现，支持笔记本管理、来源添加、提问、生成播客/视频等功能。

## 使用方法

**触发词**: `notebooklm`, `笔记本`, `notebook`, `播客`, `podcast`

## 参数

- **action** (`str`) (必需): 操作类型: status, login, list, create, use, ask, source_add, source_list, source_fulltext, source_guide, generate_audio, generate_video, generate_quiz, artifact_list, artifact_wait, download, delete
- **notebook_id** (`str`) (必需): 笔记本 ID
- **title** (`str`) (必需): 笔记本标题（用于创建或查找）
- **question** (`str`) (必需): 提问内容
- **source_url** (`str`) (必需): 来源 URL（网页/YouTube/文件路径）
- **source_id** (`str`) (必需): 来源 ID
- **source_ids** (`list`) (必需): 多个来源 ID，用于指定提问或生成的来源范围
- **instructions** (`str`) (必需): 生成指令（用于播客/视频）
- **artifact_id** (`str`) (必需): 内容 ID（用于等待或下载）
- **artifact_type** (`str`) (必需): 下载类型: audio, video, report, mind-map, data-table, quiz, flashcards
- **output_path** (`str`) (必需): 下载输出路径
- **research_query** (`str`) (必需): 网络研究查询
- **research_mode** (`str`) (必需): 研究模式: fast, deep
- **new_conversation** (`bool`) (必需): 是否开启新对话

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
