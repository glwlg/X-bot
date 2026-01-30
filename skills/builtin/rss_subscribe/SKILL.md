---
name: rss_subscribe
description: 订阅 RSS/Atom 源，有更新时推送
triggers:
- rss
- 订阅
- subscribe
- feed
---
# Rss Subscribe

RSS 订阅 Skill - 订阅 RSS/Atom 源

## 使用方法

**触发词**: `订阅`, `subscribe`, `rss`, `atom`, `feed`

## 参数

- **action** (`str`) (必需): 操作类型：add (添加), list (列表), remove (删除), refresh (刷新)
- **url** (`str`) (必需): RSS 源的 URL（添加或删除时需要）

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
