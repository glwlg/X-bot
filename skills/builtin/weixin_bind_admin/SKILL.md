---
api_version: v3
name: weixin_bind_admin
description: 管理员微信绑定工具。用于生成额外绑定二维码和查看已绑定微信用户。
triggers:
- wxbind
- 微信绑定
- weixin_bind
platform_handlers: true
manager_only: true
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Weixin Bind Admin

管理员专用微信绑定技能。

## Commands

- `python scripts/execute.py qr`
- `python scripts/execute.py list`

## Rules

- 仅管理员可用。
- 依赖已启用的 `weixin` 适配器。
