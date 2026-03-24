---
api_version: v3
name: stock_watch
description: "**自选股助手**。通过内置股票服务管理当前用户的自选股，并查询实时行情。"
triggers:
- stock
- 股票
- 自选股
- add_stock
- remove_stock
policy_groups:
- finance
platform_handlers: true
scheduled_jobs: true
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Stock Watch (自选股服务)

此技能已经自带完整脚本与服务层，入口是 `scripts/execute.py`。不要自行写临时 Python/Node 脚本，不要手工挑选用户数据文件路径。

## 关键约束

- 用户自选股存储由 `core.state_store` 内部管理，数据落在 Bot 自己的数据目录中。
- **禁止** 自行创建 `~/.ikaros/stocks.json`、临时 SQLite、Markdown 或其他自定义持久化文件。
- 当前用户默认从运行时注入的 `X_BOT_RUNTIME_USER_ID` 读取；只有注入缺失时才手工传 `--user-id`。
- 当前平台默认从 `X_BOT_RUNTIME_PLATFORM` 读取；为空或为 `subagent_kernel` 时自动回落到 `telegram`。

## 使用方式

通过 `bash` 在技能目录执行：

```bash
cd skills/builtin/stock_watch
python scripts/execute.py <subcommand> [args]
```

如果运行时没有自动注入用户上下文，再显式补参数：

```bash
cd skills/builtin/stock_watch
python scripts/execute.py --user-id 123456 add NVDA
```

## 支持的子命令

- `list`
  读取当前用户自选股，并输出最新行情。
- `refresh`
  `list` 的别名。
- `quotes [CODE ...]`
  查询显式股票代码；如果不传代码，则查询当前用户自选股。
- `search <keyword>`
  根据股票名称或代码搜索候选项。
- `add <keyword>`
  搜索并加入自选股；如果匹配到多个候选，会返回候选列表，之后应让用户明确选择。
- `remove <keyword>`
  按代码、精确名称或模糊名称删除当前用户自选股。

## 公共参数

- `--user-id <id>`
  仅在 `X_BOT_RUNTIME_USER_ID` 缺失时使用。
- `--platform <name>`
  可选，默认读取运行时平台；常见值如 `telegram`、`discord`。

## 推荐 SOP

1. 查看/刷新自选股：直接执行 `python scripts/execute.py list`。
2. 添加股票前，如果名称可能有歧义，先执行 `search`；确认后再执行 `add`。
3. 删除股票时优先传股票代码；只有用户只给了自然语言名称时才用模糊删除。
4. 脚本已经封装了行情查询与存储，不要绕过它自行请求股票 API。

## 示例

```bash
cd skills/builtin/stock_watch
python scripts/execute.py list
python scripts/execute.py search 宁德时代
python scripts/execute.py add NVDA
python scripts/execute.py remove TSLA
python scripts/execute.py quotes sh600519 sz000001
```
