---
api_version: v3
name: searxng_search
description: "**聚合网络搜索**。基于 SearXNG，支持 Google/Bing/DuckDuckGo 等多引擎聚合搜索。"
triggers:
- search
- 搜索
- 查找
- find
- google
- 百度
- 谷歌
input_schema:
  type: object
  properties:
    query:
      type: string
      description: 单条搜索关键词（与 queries 二选一）
    queries:
      type: array
      description: 并行搜索关键词列表（推荐）
      items:
        type: string
    intent_profile:
      type: string
      description: 意图配置档（weather/news/tech/general），可覆盖自动识别
    num_results:
      type: integer
      description: 每条 query 返回的结果数（1-10，默认 5）
      minimum: 1
      maximum: 10
      default: 5
    categories:
      type: string
      description: SearXNG 分类，例如 general、news、it、science
      default: general
    time_range:
      type: string
      description: 时间范围 day/week/month/year
    language:
      type: string
      description: 搜索语言，例如 zh-CN、en-US
      default: zh-CN
    engines:
      type: array
      description: 指定搜索引擎列表（如 ["google", "bing"]）
      items:
        type: string
    site_allowlist:
      type: array
      description: 结果来源域名白名单（用于提权或严格过滤）
      items:
        type: string
    site_blocklist:
      type: array
      description: 结果来源域名黑名单（过滤噪声站点）
      items:
        type: string
    strict_sources:
      type: boolean
      description: 启用严格来源过滤（仅保留白名单域名，若命中为空则自动回退）
permissions:
  filesystem: workspace
  shell: false
  network: limited
entrypoint: scripts/execute.py
---

# SearXNG Search (网络搜索)

你是一个网络搜索专家。

## 核心能力

1.  **聚合搜索**: 能够同时检索多个搜索引擎的结果。
2.  **多角度搜索**: 支持并行搜索多个关键词 (`queries`)，并生成聚合报告。

## 执行指令 (SOP)

### 参数说明

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `query` | string | 条件 | 单一搜索关键词 (与 `queries` 二选一) |
| `queries` | list | 条件 | **推荐**。并行搜索关键词列表 (例如 `["Python 教程", "Python 最佳实践"]`) |
| `intent_profile` | string | 否 | 意图配置档: `weather` / `news` / `tech` / `general` |
| `num_results` | int | 否 | 返回结果数量 (默认 5) |
| `categories` | string | 否 | 分类: `general` (默认), `news`, `it`, `science`, `images` |
| `time_range` | string | 否 | 时间范围: `day`, `week`, `month`, `year` |
| `language` | string | 否 | 语言: `zh-CN` (默认), `en-US` |
| `engines` | list[string] | 否 | 指定引擎（例如 `google,bing`） |
| `site_allowlist` | list[string] | 否 | 域名白名单（提升或限制来源） |
| `site_blocklist` | list[string] | 否 | 域名黑名单（过滤噪声站点） |
| `strict_sources` | bool | 否 | 严格来源模式，优先只保留白名单来源 |

### 降噪策略（内置）

- 对天气类查询自动做来源提权（如中国天气网、IQAir、国家级气象域名）。
- 支持 `site_allowlist/site_blocklist` 做域名级过滤，减少百科/内容农场噪声。
- 对结果做去重与重排，再返回摘要与 HTML 聚合报告。

### 多意图配置档

- `weather`: 天气/体感/AQI/紫外线等查询，默认偏实时与权威气象源。
- `news`: 新闻/头条/快讯查询，默认使用新闻分类与新闻源配置。
- `tech`: 技术问答/报错排查/API 文档查询，默认偏文档与开发者站点。
- `general`: 通用搜索。

优先级：`显式参数` > `intent_profile` > `自动意图识别` > `general`。

### 意图映射示例

**1. 简单搜索**
- 用户输入: "搜索 Linux 常用命令"
- 提取参数:
  ```json
  { "query": "Linux 常用命令" }
  ```

**2. 多角度搜索 (推荐)**
- 用户输入: "帮我对比一下 Python 和 Golang 的优缺点"
- 提取参数:
  ```json
  { "queries": ["Python 优缺点", "Golang 优缺点", "Python vs Golang 性能对比"] }
  ```

**3. 搜索特定类型 (新闻)**
- 用户输入: "搜索最近关于 SpaceX 的新闻"
- 提取参数:
  ```json
  { "query": "SpaceX", "categories": "news", "time_range": "month" }
  ```
