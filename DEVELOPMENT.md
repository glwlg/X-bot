# Ikaros DEVELOPMENT

更新时间：2026-03-24  
状态：`ACTIVE`

本文描述当前仓库已经落地的运行时边界、extension 分层和开发约束。若文档与代码冲突，以现有实现为准。

## 1. 当前系统形态

Ikaros 当前的主运行形态是两类进程：

- `ikaros`：唯一用户可见的 Core Manager
- `ikaros-api`：FastAPI + SPA

Manager 运行在宿主机或单容器内，必要时在同进程内启动受控 `subagent` 做并发执行。  
`subagent` 不是独立部署单元，也不是直接对用户交付结果的 agent。

## 2. 职责边界

### 2.1 Core Manager

Manager 负责：

- 平台无关的消息上下文、提示词、SOUL、工具面组装
- 请求路由、skill 缩圈、任务治理、heartbeat、记忆、权限控制
- 直接执行普通请求
- 在需要并发或风险隔离时启动内部 `subagent`
- 初始化 extension runtime，并按顺序加载 memory、channel、skill、plugin
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
- Core 不再直接写 channel / memory / skill 的业务注册分支；应通过 extension runtime 暴露的统一注册函数完成扩展注入

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

`ikaros-api` 负责：

- `/api/v1/*` 路由
- auth、binding、accounting 等 Web/API 能力
- 前端静态资源和 SPA fallback

## 3. 代码结构

```text
src/
├── api/               # FastAPI + SPA
├── core/              # orchestrator、runtime、state/task/subagent、platform 抽象、extension runtime
├── handlers/          # 可复用的命令与消息处理逻辑
├── manager/           # manager 侧开发/规划/闭环服务
├── platforms/
│   └── web/           # Web 前端与静态资源
├── services/          # LLM、下载、搜索、统一路由等外部服务
└── shared/            # 通用契约与跨模块共享类型

extension/
├── channels/          # Telegram / Discord / DingTalk / Weixin 渠道扩展
├── memories/          # file / mem0 等记忆扩展
├── plugins/           # 普通扩展
└── skills/            # builtin / learned skills
```

补充：

- `src/core/platform/` 只保留平台无关抽象、统一消息模型和 adapter registry
- Telegram / Discord / DingTalk / Weixin 的 bot 渠道实现已迁到 `extension/channels/*`
- `src/platforms/web/` 保留 Web 前端与静态资源，不属于 bot 渠道扩展

关键入口：

- `src/main.py`：Manager 主程序，负责 extension runtime 启动顺序
- `src/api/main.py`：API 主程序
- `src/core/extension_runtime.py`：统一注册函数、生命周期和 active memory 挂载
- `src/core/extension_base.py`：`BaseExtension` / `SkillExtension` / `ChannelExtension` / `MemoryExtension` / `PluginExtension`
- `extension/channels/registry.py`：渠道扩展发现与注册
- `extension/memories/registry.py`：记忆扩展发现与激活
- `extension/plugins/registry.py`：普通扩展发现与注册
- `extension/skills/registry.py`：skill 索引、`SKILL.md` 解析、代码型 skill 注册
- `src/core/long_term_memory.py`：长期记忆服务，从 active memory extension 获取 provider
- `src/services/intent_router.py`：统一请求路由，输出 `request_mode + candidate_skills`
- `src/core/agent_orchestrator.py`：LLM function-call 编排
- `src/core/orchestrator_runtime_tools.py`：工具装配与执行策略
- `src/core/orchestrator_context.py`：task/session 运行时上下文
- `src/core/subagent_supervisor.py`：内部 `subagent` 启动、等待、后台交付
- `src/core/model_config.py`：`config/models.json` 读写、角色模型解析与运行时重载
- `src/core/llm_usage_store.py`：LLM 用量聚合存储、token 估算与 OpenAI client 包装

## 4. Extension Runtime 约束

### 4.1 分类

当前 extension 体系分四类：

- `extension/skills`
  - 真源是 `SKILL.md`
  - skill metadata、prompt、trigger、`tool_exports` 从 `SKILL.md` 解析
  - 带代码注册能力的 skill 通过 `SkillExtension` 子类注册命令、回调、job
  - `/skills`、`/reload_skills` 和 skill 菜单回调由 skill registry 注入
  - Telegram 的 `/teach` skill 创建流程也由 skill registry 负责安装

- `extension/channels`
  - 通过 `ChannelExtension` 子类注册 adapter、消息路由、平台命令和渠道业务能力
  - 可同时启用多个 channel
  - 微信绑定 `/wxbind` 归属 Weixin channel extension

- `extension/memories`
  - 通过 `MemoryExtension` 子类提供长期记忆 provider
  - 启动时只允许一个 enabled provider
  - `long_term_memory` 不再在内部硬编码 provider switch

- `extension/plugins`
  - 普通扩展，没有额外抽象层
  - 当前 `/start`、`/help`、`/task`、`/model`、`/usage` 等控制面命令和菜单从这里注入

### 4.2 基类与最小接口

当前最小扩展接口定义在 `src/core/extension_base.py`：

- `BaseExtension`
  - `name`
  - `priority`
  - `enabled(runtime) -> bool`
  - `register(runtime) -> None`
- `SkillExtension`
  - 额外字段：`skill_name`
- `ChannelExtension`
  - 额外字段：`platform_name`
- `MemoryExtension`
  - 额外字段：`provider_name`
  - 额外方法：`create_provider(runtime) -> provider`
- `PluginExtension`

约束：

- 扩展自己调用 runtime 注册函数完成注入
- 不是 core 去找每种扩展的专用导出函数
- skill 继续保留 `SKILL.md` 作为 agent/SOP 真源；其他扩展类型不引入额外 md/yaml manifest

### 4.3 统一注册面

当前 runtime 暴露的核心注册能力包括：

- `register_adapter(...)`
- `register_command(...)`
- `register_callback(...)`
- `register_job(...)`
- `on_startup(...)`
- `on_shutdown(...)`
- `activate_memory_provider(...)`

约束：

- 不要重新增加 `register_skill_handlers`、`register_skill_jobs` 这类类型特化 helper
- 新的扩展能力优先复用现有 runtime 注册面；只有确实无法表达时才扩展 runtime

### 4.4 发现规则

- `extension/skills/registry.py`
  - 扫描 `extension/skills/**/SKILL.md`
  - 索引 skill metadata、alias、`tool_exports`
  - 如 skill 目录下存在 `scripts/*.py`，继续扫描其中的 `SkillExtension` 子类

- `extension/channels/registry.py`
  - 扫描 `extension/channels/*/channel.py`
  - 加载其中定义的 `ChannelExtension` 子类

- `extension/memories/registry.py`
  - 扫描 `extension/memories/*.py`
  - 加载其中定义的 `MemoryExtension` 子类
  - 启动时必须且只能激活一个 enabled extension

- `extension/plugins/registry.py`
  - 扫描 `extension/plugins/*.py`
  - 加载其中定义的 `PluginExtension` 子类

## 5. 启动顺序

当前 `src/main.py` 的启动链路固定为：

1. 初始化数据库与基础状态存储
2. 启动 scheduler，并加载持久化 reminder / cron job
3. 初始化 extension runtime
4. 激活唯一 memory extension
5. 初始化 `long_term_memory`
6. 扫描 skills，建立 skill 索引
7. 注册 channel extensions
8. 注册 skill extensions
9. 注册 plugin extensions
10. 启动动态 skill scheduler
11. 启动 heartbeat worker
12. 运行 extension startup hooks
13. 启动 adapters
14. 启动 subagent supervisor

约束：

- 所有用户入口注册必须在 adapter start 前完成
- 平台 scoped 注册必须在对应 channel adapter 已挂入 registry 后完成
- 新增扩展能力时，不应回写 `src/main.py` 的类型特化分支

## 6. 请求路由与执行主路径

### 6.1 Unified Routing

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

### 6.2 Orchestrator Flow

`AgentOrchestrator` 的顺序固定为：

1. 先由 `extension_router` 给出全量 extension candidates
2. 再调用 `intent_router.route(...)`
3. 用 `candidate_skills` 缩圈 skill 候选
4. 用 `request_mode` 决定是否启用 task/session tracking

约束：

- 普通请求默认仍由 Manager 直接处理
- 只有存在并发收益或隔离需求时才启动 `subagent`
- `subagent` 运行在同进程内，由 `SubagentSupervisor` 托管

### 6.3 Chat vs Task Tracking

`task_inbox` 只记录真实任务，不再承担聊天转录职责。

当 `request_mode=chat` 时，orchestrator 必须跳过：

- `ensure_task_inbox`
- `mark_manager_loop_started`
- `activate_session`
- session event 写入
- task 状态写入

当 `request_mode=task` 时，保留现有 task/session/closure 路径。

### 6.4 Vision 输入归一化

图片输入必须在消息入口完成归一化，再交给 orchestrator 和 LLM 适配层。

当前实现约束：

- 入口解析位于 `src/handlers/ai_handlers.py` 和 `src/handlers/message_utils.py`
- 远程图片下载与 MIME 校验位于 `src/services/image_input_service.py`
- 远程图片只支持 `http/https`
- 单轮最多带入 5 张图片
- 单张默认大小上限 8 MB
- 检测到图片引用但 0 张成功加载时，必须直接报错

明确原则：

- 图片下载、路径读取、MIME 判定属于确定性预处理，不属于 skill，也不属于 `subagent`
- 不要在 LLM 工具调用阶段再去补做图片下载

## 7. Task / Session / Heartbeat 语义

状态语义：

- `pending`
- `planning`
- `running`
- `waiting_user`
- `waiting_external`
- `completed`
- `failed`
- `cancelled`
- `heartbeat`

约束：

- 不要把某个中间动作完成误写成 `completed`
- heartbeat 自动推进前必须先通知用户
- `/task` 与 `task_tracker` 只展示真实 task，不展示普通聊天轮次

### 7.1 Task Inbox 存储边界

`data/task_inbox/tasks/*.json` 是任务级真源。  
运行时查询应基于 task 文件中的 `TaskEnvelope.events`，而不是全局 `events.jsonl`。

### 7.2 Legacy Task Event Log

`data/task_inbox/events.jsonl` 已降级为 legacy 文件：

- 默认不再写入
- 若历史文件存在，启动维护时转存到 `data/task_inbox/archive/`
- 代码不应再把它当作运行态查询入口

### 7.3 Model Config 与 LLM 用量统计

模型配置的单一真源是 `config/models.json`：

- 角色模型：`primary`、`routing`、`vision`、`image_generation`、`voice`
- provider 连接信息与模型池都在同一个配置文件中维护
- 运行时切换应通过 `model_config` 或 `/model` 完成，并触发进程内重载

LLM 用量统计当前约束：

- 命令入口是 `/usage`
- 存储真源是 `data/bot_data.db` 的聚合表，不再向 `events.jsonl` 逐条追加
- 聚合粒度是 `day + session_id + model_key`

## 8. 审计与版本快照

`audit_store` 的职责是“有限回滚窗口 + 可审计”，不是无限历史。

当前落盘模型：

- `data/kernel/audit/index/*.json`：per-target 版本索引真源
- `data/kernel/audit/logs/YYYY-MM-DD.jsonl`：按天分片的审计流水
- `data/kernel/versions/**.bak`：可回滚快照文件

## 9. Skill 运行时约束

Skill 仍是一等运行时扩展，但物理位置已迁到：

- `extension/skills/builtin/`
- `extension/skills/learned/`

标准调用链：

1. 模型调用 `load_skill`
2. 读取 `SKILL.md`
3. 按 SOP 用 `bash` 执行 `python scripts/execute.py ...`

若 skill frontmatter 声明 `tool_exports`，则可以被动态注入为 direct tool。  
Manager 是否给 `subagent` 分配某个 skill，由 `allowed_skills` 决定。

约束：

- 不要重新引入顶层 `skills/` 目录作为运行时真源
- 不要重新引入 `src/core/skill_loader.py`
- 不要把 skill 命令 / 定时任务注册重新写回 core 特化函数
- 代码型 skill 应通过 `SkillExtension` 子类完成注册

## 10. Anti-Patterns

不要做以下事情：

- 不要把新的用户侧业务注册直接写进 `src/main.py`
- 不要把 channel / memory / skill 业务逻辑重新塞回 `src/core`
- 不要为 channel / memory / plugin 重新设计一套额外 manifest
- 不要绕过 `state_store` / `state_paths` 使用 ad-hoc 文件路径
- 不要重新引入独立 Worker 执行面或过时的 manager/worker 分裂架构
- 不要把新的聚合型运行时数据写回无界 JSONL
- 不要在语义判定上回退到 regex/关键词硬编码路由
