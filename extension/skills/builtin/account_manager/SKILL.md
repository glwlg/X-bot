---
api_version: v3
name: account_manager
description: 安全地管理用户账号信息（CRUD）。支持存储密码、API Key、Cookies 等敏感信息，并支持 TOTP (MFA) 代码生成。所有涉及凭证存储的操作必须优先使用此技能。
triggers:
- 账号
- account
- 账户
- login
- 登录
- 密码
input_schema:
  type: object
  properties: {}
platform_handlers: true
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Account Manager

通过 `bash` 调用本目录下的 CLI，不要自己实现账号存储逻辑。运行时会自动注入 `X_BOT_RUNTIME_USER_ID` 和 `X_BOT_RUNTIME_PLATFORM`；手工测试时可自行传 `--user-id` / `--platform`。

## Commands

- 列出账号：`python scripts/execute.py list`
- 查看账号：`python scripts/execute.py get <service>`
- 添加或更新账号：`python scripts/execute.py add <service> --data 'username=test password=123'`
- 删除账号：`python scripts/execute.py remove <service>`

## Data Format

- `--data` 支持 JSON 字符串，例如：`--data '{"username":"alice","password":"secret"}'`
- 也支持空格分隔的 `key=value`，例如：`--data 'username=alice password=secret mfa_secret=BASE32'`
- 对于 `wechat_official_account` 这类服务，可以附加自定义字段；例如统一配置公众号文章作者：`--data 'app_id=xxx app_secret=yyy author=炜煜'`。`news_article_writer` 会把它同时用于文章作者和配图水印（自动变成 `@炜煜`）。

## Rules

- 凭证存储位置由代码内部管理，不要自定义文件路径。
- 用户明确要求查看某个账号时，再执行 `get`。
- 只做账号信息的增删改查，不负责账号注册。
