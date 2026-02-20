---
trigger: always_on
description: X-Bot 项目开发规范与注意事项
---

# X-Bot 开发规范

## 1. 核心架构与环境
- **运行环境**：
    - 必须使用 **Docker Compose** 运行：`docker compose up --build -d`
    - 依赖管理：使用 `uv`，Python 版本 3.14。
    - **禁止直接运行 `python` 命令**，必须使用 `uv run` 或在 Docker 中运行。
- **数据持久化**：
    - 数据库：使用 SQLite (`bot_data.db`) 存储所有业务数据。
    - 存储卷：`docker-compose.yml` 必须配置 `./data:/app/data` 和 `./downloads:/app/downloads`。
- **异步编程**：
    - 所有 I/O 操作（HTTP 请求、数据库查询、文件读写）必须使用 `async/await`。

## 2. 代码规范
- **常量提取**：长文本（欢迎语、Prompt）需提取为全局常量，避免硬编码。
- **交互体验**：耗时操作需发送临时状态消息（"Processing..."）。

## 3. Git 规范
- **忽略文件**：`.env`, `data/`, `downloads/` 必须被 gitignore。

## 4. 开发规范
- **消息发送**：一律使用smart_edit_text和smart_reply_text来编辑或发送文本消息，而非直接调用sdk的接口
- **权限控制**：所有用户交互入口（Command, Message, Callback）必须首先调用 `check_permission_unified` (或 `is_user_allowed` 等价逻辑) 校验权限。
- **工作完整**：完成一项开发任务之后执行`docker compose down && docker compose up --build -d`重新发布服务

## 5. 安全规范
- **账号存储**：严禁将任何账号、密码、Token 等敏感信息直接写入代码、配置文件或记忆文件（memory）。必须使用 `account_manager` skill 进行存储和读取。