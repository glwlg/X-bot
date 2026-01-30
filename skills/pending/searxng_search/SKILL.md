---
name: searxng_search
description: 通过本地 SearXNG 实例进行网络搜索，支持分类、时间范围筛选。
triggers:
- search
- 搜索
- 查找
- find
- google
---
# Searxng Search

SearXNG 网络搜索 Skill - 通过本地部署的 SearXNG 进行网络搜索

## 使用方法

**触发词**: `搜索`, `search`, `查询`, `谷歌`, `百度`

## 参数

- **query** (`str`) (必需): 搜索关键词
- **num_results** (`int`) (必需): 返回结果数量 (1-10)
- **categories** (`str`) (必需): 搜索分类: general, news, it, science, files, images, videos, social media, map
- **time_range** (`str`) (必需): 时间范围: day, week, month, year
- **language** (`str`) (必需): 搜索语言 (如 zh-CN, en-US)

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
