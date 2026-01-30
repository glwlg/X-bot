---
name: monitor_keyword
description: 监控新闻中的指定关键词，有新消息时推送
triggers:
- monitor
- 监控
- watch
- 关注
---
# Monitor Keyword

监控关键词 Skill - 监控新闻中的指定关键词

## 使用方法

**触发词**: `监控`, `monitor`, `关注新闻`, `跟踪`, `追踪`

## 参数

- **action** (`str`) (必需): 操作类型：add (添加), list (列表), remove (删除)
- **keyword** (`str`) (必需): 要监控的关键词（添加或删除时需要）

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
