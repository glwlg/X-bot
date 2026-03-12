---
api_version: v3
name: rss_subscribe
description: 管理 RSS 订阅。
triggers:
- rss
- 订阅
- subscribe
- feed
- 关注
policy_groups:
- research
- feeds
platform_handlers: true
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
      description: add=新增 RSS 订阅，list=查看订阅，remove=取消订阅，refresh=检查最新更新
    url:
      type: string
      description: action=add/remove 时使用；传 RSS URL 或订阅 ID
  required:
    - action
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# RSS Subscribe

通过 `bash` 调用脚本统一管理 RSS 订阅。

## Commands

- 查看订阅：`python scripts/execute.py list`
- 添加 RSS：`python scripts/execute.py add <rss_url>`
- 删除订阅：`python scripts/execute.py remove <subscription_id>`
- 手动刷新：`python scripts/execute.py refresh`

## Rules

- `refresh` 只做即时检查，不会修改调度频率。
