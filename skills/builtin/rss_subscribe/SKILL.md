---
api_version: v3
name: rss_subscribe
description: 管理 RSS 订阅和关键词新闻监控。
triggers:
- rss
- 订阅
- subscribe
- feed
- monitor
- 监控
- watch
- 关注
input_schema:
  type: object
  properties:
    action:
      type: string
      enum:
        - add
        - list
        - remove
        - refresh
      description: add=新增订阅/关键词监控，list=查看订阅，remove=取消订阅，refresh=检查最新更新
    url:
      type: string
      description: action=add/remove 时使用；可传 RSS URL 或监控关键词
  required:
    - action
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# RSS Subscribe

通过 `bash` 调用脚本统一管理 RSS 和关键词监控。

## Commands

- 查看订阅：`python scripts/execute.py list`
- 添加 RSS：`python scripts/execute.py add <rss_url>`
- 监控关键词：`python scripts/execute.py monitor "DeepSeek, OpenAI"`
- 删除订阅：`python scripts/execute.py remove <rss_url_or_keyword>`
- 手动刷新：`python scripts/execute.py refresh`

## Rules

- 明确是关键词监控时优先用 `monitor`，不要把它伪装成普通 URL。
- `refresh` 只做即时检查，不会修改调度频率。
