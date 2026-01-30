---
name: stock_watch
description: 管理用户自选股列表，支持添加、删除、查看股票
triggers:
- stock
- 股票
- 自选股
- add_stock
- remove_stock
---
# Stock Watch

自选股 Skill - 管理用户的股票关注列表

## 使用方法

**触发词**: `关注`, `自选股`, `盯盘`, `添加股票`, `取消关注`

## 参数

- **action** (`str`) (必需): 操作类型
- **stock_name** (`str`): 股票名称

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
