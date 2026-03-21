# X-Bot DEVELOPMENT

更新时间：2026-03-19  
状态：`ACTIVE`

本文描述当前仓库已经落地的运行时边界与开发约束。若文档与代码冲突，以现有实现为准。

## 1. 当前系统形态

X-Bot 当前的主运行形态是两类进程：

- `x-bot`：唯一用户可见的 Core Manager
- `x-bot-api`：FastAPI + SPA

Manager 运行在宿主机或单容器内，必要时在同进程内启动受控 `subagent` 做并发执行。  
`subagent` 不是独立部署单元，也不是直接对用户交付结果的 agent。

## 2. 职责边界

### 2.1 Core Manager

Manager 负责：

- 平台消息入口和命令入口
- 提示词、SOUL、上下文、工具面组装
- 请求路由、skill 缩圈、任务治理、heartbeat、记忆、权限控制
- 直接执行普通请求
- 在需要并发或风险隔离时启动内部 `subagent`
- 统一接收 `subagent` 结果并决定继续、降级、重试、等待用户或最终交付
- 编码会话、仓库工作区、git/gh 发布与本地 rollout

Manager 的基础原语：

- `read`
- `write`
- `edit`
- `bash`
- `load_skill`

Manager 内部控制面工具：

- `spawn_subagent`
- `await_subagents`

Manager 侧常用 direct tool：

- `repo_workspace`
- `codex_session`
- `git_ops`
- `gh_cli`
- `task_tracker`

约束：

- 用户最终只和 Manager 对话
- `subagent` 不能直接向平台发消息
- `subagent` 只能使用 Manager 显式分配的工具与技能
- `subagent` 失败后必须先回 Manager 决策，不能直接把原始失败结果当作最终交付

### 2.2 Internal Subagent

`subagent` 负责：

- 执行一个边界清晰的子任务
- 在受控工具集内完成局部目标
- 返回结构化结果、附件和诊断信息

`subagent` 不负责：

- 直接面向用户回复
- 继续拆分出新的 `subagent`
- 自己决定任务闭环是否成立

### 2.3 API Service

`x-bot-api` 负责：

- `/api/v1/*` 路由
- auth、binding、accounting 等 Web/API 能力
- 前端静态资源和 SPA fallback

## 3. 代码结构

```text
src/
├── api/          # FastAPI + SPA
├── core/         # orchestrator、prompt、tool/runtime、state/task/subagent
├── handlers/     # 用户命令与消息入口
├── manager/      # manager 侧开发/规划/闭环服务
├── platforms/    # Telegram / Discord / DingTalk 适配
├── services/     # LLM、下载、搜索、统一路由等外部服务
├── shared/       # 通用契约与跨模块共享类型
```

关键入口：

- `src/main.py`：Manager 主程序
- `src/api/main.py`：API 主程序
- `src/services/intent_router.py`：统一请求路由，输出 `request_mode + candidate_skills`
- `src/core/agent_orchestrator.py`：LLM function-call 编排
- `src/core/orchestrator_runtime_tools.py`：工具装配与执行策略
- `src/core/orchestrator_context.py`：task/session 运行时上下文
- `src/core/subagent_supervisor.py`：内部 `subagent` 启动、等待、后台交付
- `src/manager/relay/closure_service.py`：阶段任务闭环、`waiting_user`/`waiting_external`/`completed` 决策
- `src/core/task_inbox.py`：真实任务账本与轻量保留策略
- `src/core/audit_store.py`：版本快照索引、回滚与审计流水

## 4. 请求路由与执行主路径

### 4.1 Unified Routing

统一路由入口只有 `services.intent_router`。  
`skill_router.py` 已删除，不再保留双实现。

`intent_router.route(...)` 一次性完成两件事：

- 判断本轮是 `task` 还是 `chat`
- 从 extension candidate 列表里筛出 `candidate_skills`

标准返回结构是 `RoutingDecision`：

- `request_mode`
- `candidate_skills`
- `confidence`
- `reason`
- `raw`

判定口径：

- `task`：多步执行、可能用工具或外部查询、可跟踪、可恢复、可能等待外部结果、需要 `/task` 或 follow-up/closure
- `chat`：闲聊、轻问答、互动小游戏、连续猜题、普通陪聊、无需闭环

兜底规则：

- 路由失败时默认回退到 `request_mode=task`
- 同时返回 `candidate_skills=[]`
- 目标是宁可少缩圈，也不要漏掉真实任务

### 4.2 Orchestrator Flow

`AgentOrchestrator` 的顺序固定为：

1. 先由 `extension_router` 给出全量 extension candidates
2. 再调用 `intent_router.route(...)`
3. 用 `candidate_skills` 缩圈 skill 候选
4. 用 `request_mode` 决定是否启用 task/session tracking

约束：

- 普通请求默认仍由 Manager 直接处理
- 只有存在并发收益或隔离需求时才启动 `subagent`
- `subagent` 运行在同进程内，由 `SubagentSupervisor` 托管

### 4.3 Chat vs Task Tracking

`task_inbox` 只记录真实任务，不再承担聊天转录职责。

当 `request_mode=chat` 时，orchestrator 必须跳过：

- `ensure_task_inbox`
- `mark_manager_loop_started`
- `activate_session`
- session event 写入
- task 状态写入

当 `request_mode=task` 时，保留现有 task/session/closure 路径。

明确原则：

- 聊天历史由会话存储维护
- `task_inbox` 只面向需要跟踪、恢复或闭环的任务

### 4.4 Vision 输入归一化

图片输入必须在消息入口完成归一化，再交给 orchestrator 和 LLM 适配层。

当前已落地的视觉输入路径：

- 用户直接发送图片：`handle_ai_photo()` 直接构造 `inline_data`
- 用户在文本消息中给出图片 URL：入口先下载并校验图片，再构造 `inline_data`
- 用户在文本消息中给出本地绝对图片路径：入口直接读取并校验图片，再构造 `inline_data`
- 用户回复一条带图片 URL 的消息：优先解析成图片输入；只有确认不是图片时才回退到网页内容抓取

当前实现约束：

- 入口解析位于 `src/handlers/ai_handlers.py` 和 `src/handlers/message_utils.py`
- 远程图片下载与 MIME 校验位于 `src/services/image_input_service.py`
- 远程图片只支持 `http/https`
- 单轮最多带入 5 张图片
- 单张默认大小上限 8 MB
- 检测到图片引用但 0 张成功加载时，必须直接报错，不能让模型按“已看图”继续回答
- 文本里只有图片 URL 或路径、没有有效文字时，应自动补默认提示词再送入模型
- 归一化后的图片统一复用现有 `inline_data -> image_url(data:...)` 适配链路与 vision 自动选模逻辑

明确原则：

- 图片下载、路径读取、MIME 判定属于确定性预处理，不属于 skill，也不属于 `subagent`
- 不要把图片 URL 当普通网页摘要去处理后再让模型“猜图”
- 不要在 LLM 工具调用阶段再去补做图片下载，这会让输入边界失真

## 5. Task / Session / Heartbeat 语义

状态语义：

- `pending`：已创建，待执行
- `planning`：已进入规划
- `running`：执行中
- `waiting_user`：当前阻塞，需要用户补充或确认
- `waiting_external`：依赖外部世界变化，不应误判为完成
- `completed`：任务真正完成并可交付
- `failed`：任务失败
- `cancelled`：任务取消
- `heartbeat`：主动回顾未闭环任务，决定是否提醒、继续或维持等待

约束：

- 不要把某个中间动作完成误写成 `completed`
- heartbeat 自动推进前必须先通知用户
- `/task` 与 `task_tracker` 只展示真实 task，不展示普通聊天轮次

### 5.1 Task Inbox 存储边界

`data/task_inbox/tasks/*.json` 是任务级真源。  
运行时查询应基于 task 文件中的 `TaskEnvelope.events`，而不是全局 `events.jsonl`。

保留策略：

- 永久保留 open task：`pending`、`planning`、`running`、`waiting_user`、`waiting_external`
- terminal task 中，若 `resume_window_until` 仍有效，则继续保留
- `heartbeat` 来源的 terminal task 终态后直接删除
- 其余 terminal task 全局只保留最近 10 个
- 单个 task 的 `events` 只保留最近 50 条

维护时机：

- `init_services()` 启动时做一次收敛
- 每次 task 进入 terminal 状态后做一次轻量裁剪

### 5.2 Legacy Task Event Log

`data/task_inbox/events.jsonl` 已降级为 legacy 文件：

- 默认不再写入
- 若历史文件存在，启动维护时转存到 `data/task_inbox/archive/`
- 代码不应再把它当作运行态查询入口

## 6. 审计与版本快照

`audit_store` 的职责是“有限回滚窗口 + 可审计”，不是无限历史。

当前落盘模型：

- `data/kernel/audit/index/*.json`：per-target 版本索引真源
- `data/kernel/audit/logs/YYYY-MM-DD.jsonl`：按天分片的审计流水
- `data/kernel/versions/**.bak`：可回滚快照文件

约束：

- `list_versions()` 和 `rollback()` 必须直接读 per-target index
- 审计流水只承担审计，不再承担版本检索
- 每个 target 只保留最近 20 个仍存在的 snapshot
- 缺失 snapshot 的脏索引会在维护时自动剔除

### 6.1 Legacy Audit Event Log

`data/kernel/audit/events.jsonl` 已降级为 legacy 输入：

- 启动维护时会导入仍可用的 `snapshot` 记录到新 index
- 导入完成后移动到 `data/kernel/audit/logs/legacy-*.jsonl`
- 代码不应继续依赖单个无限增长的 audit `events.jsonl`

## 7. Skill 系统

Skill 仍是运行时扩展，放在：

- `skills/builtin/`
- `skills/learned/`

标准调用链：

1. 模型调用 `load_skill`
2. 读取 `SKILL.md`
3. 按 SOP 用 `bash` 执行 `python scripts/execute.py ...`

若 skill frontmatter 声明 `tool_exports`，则可以被动态注入为 direct tool。  
Manager 是否给 `subagent` 分配某个 skill，由 `allowed_skills` 决定。

## 8. 正式开发链路

仓库开发优先走：

- `repo_workspace`
- `codex_session`
- `git_ops`
- `gh_cli`

约束：

- 优先独立 worktree，不要在脏工作区里直接切分支
- 若任务在编码、发布或外部系统交互后仍未闭环，必须保留未完成状态并写清完成条件
- 运行态存储改动要同时考虑启动维护、回滚窗口和历史文件迁移

## 9. 部署约束

- `docker-compose.yml` 只保留 `x-bot` 与 `x-bot-api`
- 推荐把主 bot 作为宿主机长进程运行；Docker 主要承载 API 或可选基础设施

## 10. 反模式

- 不要重新引入 `skill_router.py` 或双路由实现
- 不要重新引入独立 `Worker` 进程、共享队列执行链路或 manager/worker 双运行面
- 不要把普通聊天轮次写进 `task_inbox`
- 不要把 `task_inbox` 当作聊天记录仓库
- 不要继续依赖单个无限增长的 `data/task_inbox/events.jsonl`
- 不要继续依赖单个无限增长的 `data/kernel/audit/events.jsonl`
- 不要绕过 `state_store` / `state_paths` 直接拼运行态文件路径
- 不要让 `subagent` 直接做用户交付闭环
- 不要把 `spawn_subagent` 当成默认执行路径
- 不要把 direct tool 大量重新写死回核心注册表，优先通过 skill metadata 导出

## 11. 常用命令

```bash
uv sync
uv run python src/main.py
uv run pytest
docker compose up --build -d
docker compose logs -f x-bot
```

## 12. 高信号测试

- `tests/test_services.py`
- `tests/core/test_orchestrator_single_loop.py`
- `tests/core/test_orchestrator_delivery_closure.py`
- `tests/core/test_task_inbox.py`
- `tests/core/test_audit_store.py`
- `tests/manager/test_closure_service.py`
