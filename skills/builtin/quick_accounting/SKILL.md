---
api_version: v3
name: quick_accounting
description: 智能记账工具。识别消费、收入和转账后，必须调用本技能将记录写入账本。
triggers:
- 记账
- 消费
- 支付
- 收款
- 转账
- spend
- income
input_schema:
  type: object
  properties:
    type:
      type: string
      enum: ["支出", "收入", "转账"]
    amount:
      type: number
    category:
      type: string
    account:
      type: string
    target_account:
      type: string
    payee:
      type: string
    remark:
      type: string
    record_time:
      type: string
  required: ["type", "amount", "category", "account"]
permissions:
  filesystem: workspace
  shell: true
  network: none
entrypoint: scripts/execute.py
---

# Quick Accounting

通过 `bash` 执行 CLI 完成入账。正常运行时会根据平台绑定自动解析用户；仅在手工测试或回填场景下，才额外传 `--accounting-user-id` / `--accounting-book-id`。

## Command

- `python scripts/execute.py --type 支出 --amount 30 --category 餐饮 --account 微信 --payee 麦当劳 --remark 午餐`
- 转账示例：`python scripts/execute.py --type 转账 --amount 500 --category 转账 --account 招商银行 --target-account 支付宝`

## Rules

- `amount` 必须大于 0。
- `转账` 必须提供 `--target-account`。
- 账本和账户由服务层自动解析或创建，不要自己写数据库逻辑。
