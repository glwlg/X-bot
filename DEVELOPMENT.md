# X-Bot DEVELOPMENT

更新时间：2026-03-07
状态：`ACTIVE`

本文描述当前仓库已经落地的架构边界、运行时约束和开发入口。它不是愿景文档，默认以现有代码为准。

## 1. 当前系统形态

X-Bot 当前是三服务架构：

- `x-bot`：Core Manager
- `x-bot-worker`：默认 Worker Kernel
- `x-bot-api`：FastAPI + SPA

三者通过 `docker-compose.yml` 启动，已经拆成独立镜像 target 和独立 Python 依赖组。

## 2. 职责边界

### 2.1 Core Manager

Manager 负责：

- 平台消息入口和命令入口
- 提示词、SOUL、上下文、工具面组装
- 按权限注入原语工具与 skill direct tool
- 普通异步任务派发给 worker
- manager 侧编码会话、仓库工作区、git/gh 发布与本地 rollout
- manager 自身执行过程和 worker 结果回传
- heartbeat、记忆、治理和权限控制

Manager 当前可以直接使用这些基础原语：

- `read`
- `write`
- `edit`
- `bash`
- `load_skill`

另外，声明了 `tool_exports` 且权限允许的 skill tool 会被动态注入。例如当前的：

- `repo_workspace`
- `codex_session`
- `git_ops`
- `gh_cli`
- `list_workers`
- `dispatch_worker`
- `worker_status`

约束：

- 仓库代码路径上的正式开发优先通过 `repo_workspace` + `codex_session` + `git_ops` + `gh_cli`
- 运行态数据文件仍可用原语直接维护，例如 `data/` 下的用户状态和系统状态

### 2.2 Worker Kernel

Worker 负责：

- 从共享 dispatch queue claim 任务
- 维持 lease heartbeat
- 执行默认 program/runtime
- 写回结果、错误、摘要和进度
- 镜像运行时尽量保持精简，不再承载平台 SDK

Worker 不负责：

- 平台消息入口
- Manager 核心治理逻辑
- 直接向平台发消息

消息进出统一走 Manager，Worker 只写队列和结果。

### 2.3 API Service

`x-bot-api` 负责：

- `/api/v1/*` 路由
- auth、binding、accounting 等 Web/API 能力
- 前端静态资源和 SPA fallback

API 与 Manager/Worker 已拆成独立镜像，不再共享全部运行时依赖。

## 3. 代码结构

```text
src/
├── api/          # FastAPI + SPA
├── core/         # 提示词、工具注入、状态访问、运行时策略
├── handlers/     # 用户命令与消息入口
├── manager/      # dispatch、relay、workspace/codex/git/gh 工具
├── platforms/    # Telegram / Discord / DingTalk 适配
├── services/     # LLM、下载、搜索等外部服务
├── shared/       # queue contract / jsonl queue / shared models
└── worker/       # worker kernel + program loader/runtime
```

关键入口：

- `src/main.py`：Manager 主程序
- `src/worker_main.py`：Worker 主程序
- `src/api/main.py`：API 主程序
- `src/core/agent_orchestrator.py`：LLM function-call 编排
- `src/core/orchestrator_runtime_tools.py`：工具装配与执行策略
- `src/manager/dispatch/service.py`：worker 选择与派发
- `src/manager/relay/result_relay.py`：worker 结果和进度回传
- `src/manager/dev/workspace_session_service.py`：repo workspace / worktree 管理
- `src/manager/dev/codex_session_service.py`：manager 编码会话
- `src/manager/dev/git_ops_service.py`：git 状态/提交/push/fork
- `src/worker/kernel/daemon.py`：worker queue loop

## 4. 调度模型

### 4.1 队列协议

共享任务协议位于：

- `src/shared/contracts/dispatch.py`
- `src/shared/queue/dispatch_queue.py`

持久化路径默认是：

- `data/system/dispatch/tasks.jsonl`
- `data/system/dispatch/results.jsonl`

任务状态：

- `pending`
- `running`
- `done`
- `failed`
- `cancelled`

Worker 在 claim 任务后会续租 lease；如果 `running` 任务长期无心跳，会被恢复或标记失败。

### 4.2 Manager 派发

Manager 通过 `src/manager/dispatch/service.py` 做：

- worker 选择
- priority 计算
- load/error-rate/queue-depth 感知
- 默认 worker 创建
- 任务入队

当前选择逻辑不是 broker 级调度系统，而是基于文件队列和轻量启发式打分。

### 4.3 结果回传

Worker 完成任务后，result relay 会把结果推回原对话。

当前支持两类过程反馈：

- worker 工具过程
- manager 自身工具过程

Telegram 平台优先使用 `sendMessageDraft` 单草稿刷新，其他平台走普通消息/编辑路径。

## 5. Skill 系统

Skill 是运行时扩展，放在：

- `skills/builtin/`
- `skills/learned/`

每个 skill 至少包含：

- `SKILL.md`
- 可选 `scripts/execute.py`

### 5.1 默认调用链

默认不是“所有 skill 都变成 tool”。当前的标准调用链是：

1. 模型调用 `load_skill`
2. 读取 `SKILL.md`
3. 按 SOP 用 `bash` 执行 `python scripts/execute.py ...`

### 5.2 Direct Tool 导出

如果 skill frontmatter 声明了 `tool_exports`，该 skill 可以被动态注入为 direct tool。

相关实现：

- `src/core/skill_loader.py`
- `src/core/tool_registry.py`
- `src/core/skill_tool_handlers.py`

这意味着 direct tool 不再应写死在 `tool_registry.py`。

### 5.3 Skill Frontmatter

当前有效的 skill 元数据包括但不限于：

- `entrypoint`
- `input_schema`
- `contract`
- `tool_exports`
- `policy_groups`
- `platform_handlers`
- `prompt_hint`

约束：

- direct tool 能力应优先通过 `tool_exports` 声明
- skill 权限分组应优先在 skill 元数据中声明，而不是继续把分类硬编码到核心
- 平台 handler 注册是显式 opt-in，只有声明 `platform_handlers: true` 的 skill 才会在启动时注册平台命令或 callback

## 6. 权限模型

工具权限由 `src/core/tool_access_store.py` 管理，持久化文件是：

- `data/kernel/tool_access.json`

当前默认策略：

- Core Manager 默认允许：`management`、`automation`、`coding`、`primitives`、`skill-admin`
- Worker 默认拒绝：`coding`、`management`、`automation`

设计目标：

- Manager 负责治理与编码
- Worker 负责执行
- Skill direct tool 是否暴露，既受 skill 元数据控制，也受 runtime policy 过滤

## 7. Manager Coding Toolchain

当前的正式开发链路不再使用 `software_delivery`。Manager 应直接组合以下工具：

- `repo_workspace`
- `codex_session`
- `git_ops`
- `gh_cli`

对应实现入口：

- `src/manager/dev/workspace_session_service.py`
- `src/manager/dev/codex_session_service.py`
- `src/manager/dev/git_ops_service.py`
- `src/manager/dev/publisher.py`
- `src/manager/integrations/gh_cli_service.py`
- `config/deployment_targets.yaml`

约束：

- 仓库开发优先使用独立 worktree，不要在脏工作区里直接切分支
- Codex 提问时应进入 `waiting_user`，由 Manager 向用户转问并继续同一 coding session
- push 默认先尝试 origin，403 时自动 fallback 到 fork
- local rollout 目前基于 `docker compose build` + `docker compose up -d`
- rollout target 不再硬编码在发布器里，而是从 `config/deployment_targets.yaml` 读取

## 8. 状态与数据面

X-Bot 仍然是 file-system-first 设计。重要状态包括：

- `data/WORKERS.json`：worker 注册表
- `data/system/dispatch/`：任务与结果队列
- `data/system/dev_workspaces/` / `data/system/dev_worktrees/`：manager 开发工作区
- `data/system/codex_sessions/`：编码会话与日志
- `data/user/`：用户状态、对话、记忆、提醒、订阅等
- `data/runtime_tasks/`：heartbeat 运行态

状态访问应优先通过：

- `src/core/state_paths.py`
- `src/core/state_io.py`
- `src/core/state_store.py`

不要在业务代码里随意拼接 `data/...` 路径读写。

## 9. 平台与消息入口

当前平台入口：

- Telegram
- Discord
- DingTalk Stream

Manager 启动时会注册通用命令：

- `/start`
- `/new`
- `/help`
- `/chatlog`
- `/skills`
- `/reload_skills`
- `/stop`
- `/heartbeat`
- `/worker`
- `/acc`

Telegram 还保留：

- `/feature`
- `/teach`

但 `/teach` 已不再承担旧版“直接生成扩展代码”的职责，当前仅作过渡提示入口。

## 10. 镜像与依赖拆分

当前镜像 target：

- `manager-runtime`
- `worker-runtime`
- `api-runtime`

当前 Python 依赖组：

- `manager`
- `worker`
- `api`
- `optional-skill-runtime`

当前边界要求：

- Worker 镜像不再默认携带 Telegram/Discord/DingTalk SDK
- API 镜像不应带 manager/worker 不需要的重依赖
- 如果某类 skill 需要额外大包，优先放进可选 dependency group，而不是默认塞进所有运行时

## 11. 开发约束

### 11.1 应该做的

- 用 skill metadata 驱动 tool 暴露和权限分组
- 让 Manager 对代码类变更优先走 `repo_workspace` + `codex_session` + `git_ops` + `gh_cli`
- 保持 manager/worker/api 角色边界清晰
- 用测试保护 orchestrator、dispatch、relay、skill contract

### 11.2 不应该做的

- 不要再把 manager/worker 关键 direct tool 大量硬编码回核心注册表
- 不要让 Worker 重新承担平台消息职责
- 不要绕过 `dispatch_queue` 自己发明另一套 manager/worker 任务协议
- 不要对 `data/` 做散落的 ad-hoc 文件路径读写
- 不要把“未来想法”写成“当前已实现”放进说明文档

## 12. 常用命令

```bash
uv sync
uv run python src/main.py
uv run python src/worker_main.py
uv run uvicorn api.main:app --host 0.0.0.0 --port 8764
uv run pytest
docker compose up --build -d
docker compose logs -f x-bot
docker compose logs -f x-bot-worker
docker compose logs -f x-bot-api
```

## 13. 测试重点

高信号测试位置：

- `tests/core/test_prompt_composer.py`
- `tests/core/test_runtime_tool_skillization.py`
- `tests/core/test_orchestrator_runtime_tools.py`
- `tests/core/test_worker_result_relay.py`
- `tests/core/test_orchestrator_delivery_closure.py`
- `tests/manager/test_workspace_session_service.py`
- `tests/manager/test_codex_session_service.py`
- `tests/manager/test_git_ops_service.py`
- `tests/manager/test_deployment_targets.py`
- `tests/shared/test_dispatch_queue.py`

在修改这些区域时，至少应回归对应测试：

- tool surface / prompt / skill metadata
- dispatch queue / worker daemon
- result relay
- software delivery / rollout
