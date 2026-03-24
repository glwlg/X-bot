---
api_version: v3
name: web_search
description: 聚合网络搜索。支持单查询、多查询、来源过滤和结果报告输出。
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
      items:
        type: string
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Web Search

通过 `bash` 调用聚合搜索脚本。默认会输出摘要，并把完整报告保存为 `search_report.md`；如需改目录，加 `--output-dir`。

## Commands

- 单查询：`python scripts/execute.py "Linux 常用命令"`
- 多查询：`python scripts/execute.py "Python 优缺点" "Golang 优缺点" --num-results 5`
- 新闻或时效查询：`python scripts/execute.py "SpaceX" --categories news --time-range month`
- 严格来源过滤：`python scripts/execute.py "OpenAI API" --site-allowlist openai.com,platform.openai.com --strict-sources true`

## Rules

- 付费高级搜索后端存在时，查询词通常控制在 1-2 个。
- 需要精确来源时，用 `--site-allowlist` / `--site-blocklist`，不要在自然语言里模糊描述。
