---
name: web_browser
description: 访问网页内容或生成网页摘要。当用户提供 URL 并请求阅读、总结、或询问网页内容时使用。
triggers:
- 访问
- browse
- 打开网页
- 查看网页
- 网页
- 阅读
- read
- summarize
---
# Web Browser

网页浏览器 Skill - 访问和总结网页内容

## 使用方法

**触发词**: `浏览`, `访问`, `查看`, `总结`, `摘要`

## 参数

- **action** (`str`) (必需): 操作类型：visit (获取内容), summarize (生成摘要)
- **url** (`str`) (必需): 目标网页 URL

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
