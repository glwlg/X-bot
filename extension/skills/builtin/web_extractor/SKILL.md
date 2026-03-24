---
api_version: v3
name: web_extractor
description: 提取网页正文的清洗版 Markdown。用户只需要“读取/总结链接内容”时，优先使用本技能。
input_schema:
  type: object
  properties:
    url:
      type: string
      description: 目标网页 URL
  required:
    - url
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Web Extractor

这是正文提取 skill。通过 `bash` 调用脚本读取页面内容；如果只是读文章，不要升级到 `web_browser`。

## Command

- `python scripts/execute.py <url>`

## Rules

- 适用于摘要、正文提取、文章阅读。
- 只有在需要交互点击、登录、截图时，才换成 `web_browser`。
