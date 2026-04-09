# Ikaros

Ikaros 是一个 Python 多平台 AI Bot，当前采用 `Ikaros Core + API Service + Extension Runtime` 架构。

- `ikaros`：唯一用户可见的 Ikaros Core，负责请求编排、任务治理、模型路由、心跳、状态访问和 extension runtime
- `ikaros-api`：FastAPI + SPA，提供 Web/API 能力

运行时状态默认落在 `~/.ikaros/data/` 下，运行配置默认落在 `~/.ikaros/config/` 下；主体仍是文件系统优先，需要聚合查询的部分使用 `~/.ikaros/data/bot_data.db` 做 SQLite 存储。

![logo](logo.jpg)

## 当前能力

- 多平台接入：Telegram、Discord、钉钉 Stream、微信 iLink，以及独立 Web/API 服务
- 多模态交互：文本、图片、视频、语音、文档输入
- Extension 分层：
  - `extension/skills`：skill 真源，继续使用 `SKILL.md` 描述 SOP、参数契约和 `tool_exports`
  - `extension/channels`：渠道扩展，负责注册 adapter、消息路由和渠道专属命令
  - `extension/memories`：长期记忆 provider 扩展，启动时必须且只能有一个 active provider
  - `extension/plugins`：普通扩展，承接控制面命令、菜单和其他 runtime 注入能力
- 任务治理：真实任务具备 task/session/heartbeat 闭环，普通闲聊不写入 `task_inbox`
- 模型配置：默认使用 `~/.ikaros/config/models.json`，支持在聊天内通过 `/model` 查看和切换
- LLM 用量统计：通过 `/usage` 查看按天 + 会话 + 模型聚合的 token 使用；数据持久化到 `~/.ikaros/data/bot_data.db`
- Ikaros 开发链路：仓库类任务优先使用 `repo_workspace`、`coding_session`、`git_ops`、`gh_cli`
- 编码后端传输策略：默认采用混合模式，`codex` 继续走 CLI，`gemini-cli` 与 `opencode` 默认走 ACP；可通过 `CODING_BACKEND_*_TRANSPORT` 环境变量覆盖

## 架构概览

### 1. Ikaros Core

Ikaros 是系统统一入口，负责：

- 接收平台消息、命令和回调
- 组装提示词、SOUL、上下文和工具面
- 路由请求、缩圈 skill、维护 task/session/heartbeat
- 执行普通用户请求
- 在必要时启动同进程内的受控 `subagent`
- 初始化 extension runtime，并按顺序加载 memory、channel、skill、plugin 四类扩展

### 2. API Service

`ikaros-api` 负责：

- `/api/v1/*` 路由
- Web 认证、绑定、记账等 API 能力
- 前端静态资源与 SPA fallback

### 3. Extension Runtime

extension runtime 由 `src/core/extension_runtime.py` 和 `src/core/extension_base.py` 提供统一注册面。  
Core 只暴露运行时基础设施，不再在 core 里硬编码 channel / memory / skill 业务注册分支。

基础注册能力包括：

- `register_adapter(...)`
- `register_command(...)`
- `register_callback(...)`
- `register_job(...)`
- `on_startup(...)`
- `on_shutdown(...)`
- `activate_memory_provider(...)`

四类扩展的发现规则如下：

- `extension/skills`
  - 真源仍是 `SKILL.md`
  - `extension/skills/registry.py` 扫描 `extension/skills/**/SKILL.md`
  - skill metadata、prompt、trigger、`tool_exports` 继续从 `SKILL.md` 解析
  - 如需动态注册命令 / 回调 / job，可在 skill 的 `scripts/*.py` 中定义 `SkillExtension` 子类
  - `/skills`、`/reload_skills` 和 Telegram 的 `/teach` 流程由 skill registry 注入

- `extension/channels`
  - 渠道代码位于 `extension/channels/<platform>/channel.py`
  - 通过 `ChannelExtension` 子类注册 adapter、消息分发和渠道命令
  - 微信绑定 `/wxbind` 已归属 Weixin channel extension

- `extension/memories`
  - 通过 `MemoryExtension` 子类提供长期记忆 provider
  - `extension/memories/registry.py` 只允许一个 enabled provider 激活
  - `src/core/long_term_memory.py` 不再硬编码 provider switch

- `extension/plugins`
  - 普通扩展，没有额外抽象层
  - 当前控制面命令和菜单由这里注入

### 4. 启动顺序

当前 `src/main.py` 的启动链路为：

1. 初始化数据库与基础状态存储
2. 启动 scheduler，并加载持久化 reminder / cron job
3. 初始化 extension runtime
4. 激活唯一 memory extension，并初始化 `long_term_memory`
5. 扫描 `extension/skills/**/SKILL.md`，建立 skill 索引
6. 注册 channel extensions
7. 注册 skill extensions 与 plugin extensions
8. 启动动态 skill scheduler
9. 运行 extension startup hooks，随后启动 adapters、heartbeat 和 subagent supervisor

约束：

- 所有用户入口注册必须在 adapter start 前完成
- 新的用户侧业务入口不要写回 `src/main.py`

## 目录结构

```text
.
├── src/
│   ├── api/              # FastAPI + SPA
│   ├── core/             # orchestrator、runtime、state、task、platform 抽象、extension runtime
│   ├── handlers/         # 可复用的命令/消息处理逻辑
│   ├── ikaros/          # ikaros 侧开发/规划/闭环服务
│   ├── platforms/
│   │   └── web/          # Web 前端与静态资源
│   ├── services/         # AI、下载、搜索等外部服务集成
│   └── shared/           # 跨模块通用契约与共享类型
├── extension/
│   ├── channels/         # Telegram / Discord / DingTalk / Weixin 渠道扩展
│   ├── memories/         # file / mem0 等记忆扩展
│   ├── plugins/          # 普通扩展
│   └── skills/           # builtin + learned skills
├── data/                 # 仓库内旧数据目录；迁移后默认不再作为运行时路径
├── config/               # 仓库内模板与文档目录
├── tests/                # pytest 测试
├── docker-compose.yml
├── README.md
├── DEPLOYMENT.md
└── DEVELOPMENT.md
```

补充说明：

- Telegram / Discord / DingTalk / Weixin 的 bot 渠道实现已经迁到 `extension/channels/*`
- `src/core/platform/` 只保留平台无关抽象、统一消息模型和 adapter registry
- `src/platforms/web/` 保留 Web 前端与静态资源，不属于 bot 渠道扩展

## 部署与初始化

部署、启动、平台脚本、Docker / 非 Docker、systemd / launchd / Windows 计划任务，以及 `Wispaper` 接入说明统一收口在 [DEPLOYMENT.md](DEPLOYMENT.md)。

首次部署完成后，打开 Web 的 `/login` 完成首个管理员初始化，然后进入 `/admin/runtime` 完成管理员、`SOUL.MD`、`USER.md`、渠道和运行项配置，再到 `/admin/models` 配置模型。

## 聊天内管理命令

当前命令来源按扩展层划分如下。

### 1. `extension/plugins` 注入的控制面命令

- `/start`
- `/new`
- `/help`
- `/chatlog`
- `/compact`
- `/stop`
- `/heartbeat`
- `/task`
- `/model`
- `/usage`

### 2. `extension/skills` 注册器注入的技能管理命令

- `/skills`
- `/reload_skills`

### 3. 典型 builtin skill / channel 扩展命令

- `/acc`
- `/credential`
- `/wxbind`

### 4. 平台特有流程

- Telegram 额外保留 `/feature`
- Telegram skill 管理流包含 `/teach`
- `/wxbind` 当前由 Weixin channel extension 注册，可在 Telegram / Weixin 管理员链路中使用

### `/model`

`/model` 用于查看和切换当前模型配置，支持文本命令和按钮菜单。

常用形式：

- `/model`
- `/model show`
- `/model list`
- `/model list <role>`
- `/model use <provider/model>`
- `/model use <role> <provider/model>`

支持角色：

- `primary`
- `routing`
- `vision`
- `image_generation`
- `voice`

### `/usage`

`/usage` 用于查看 LLM 用量统计。统计口径：

- 按 `天 + 会话 + 模型` 聚合
- 所有通过 `get_client_for_model(...)`、`openai_async_client`、`openai_client` 发起的 OpenAI 兼容调用都会记账
- 上游返回 `usage` 时使用真实 token
- 上游未返回 `usage` 时，输入/输出 token 使用本地估算
- 缓存命中与缓存写入只统计上游实际返回值

统计数据写入：

- `~/.ikaros/data/bot_data.db`

## 运行时目录

- `~/.ikaros/data/`：聊天、任务、记忆、心跳、审计、SQLite 聚合数据等运行时状态
- `~/.ikaros/data/bot_data.db`：Web/API 与 LLM 用量等聚合型 SQLite 数据
- `downloads/`：媒体下载产物
- `extension/`：四类运行时扩展
- `~/.ikaros/config/`：结构化运行配置，当前主要包括 `models.json`、`memory.json` 和 `deployment_targets.yaml`
- 仓库 `config/`：运行配置模板与文档，例如 `models.example.json`

## 手动迁移旧目录

这次运行时默认不再读取仓库内 `./data` 和 `./config`。升级现有部署时，请手动迁移需要保留的运行文件：

```bash
mkdir -p ~/.ikaros/data ~/.ikaros/config
cp -R data/. ~/.ikaros/data/
[ -f config/models.json ] && cp config/models.json ~/.ikaros/config/models.json || cp config/models.example.json ~/.ikaros/config/models.json
[ -f config/memory.json ] && cp config/memory.json ~/.ikaros/config/memory.json || cp config/memory.example.json ~/.ikaros/config/memory.json
[ -f config/deployment_targets.yaml ] && cp config/deployment_targets.yaml ~/.ikaros/config/deployment_targets.yaml
```

`.env` 仍保留在仓库根目录。

## 当前维护原则

- 文档以当前实现为准，不保留未落地的旧架构描述
- 普通用户执行统一走 Ikaros Core，不重新引入独立 Worker 执行面
- 新的用户侧业务注册，不要写回 `src/main.py` 或 core 特化逻辑，优先通过 extension runtime 注入
- skill 真源始终是 `extension/skills/**/SKILL.md`
- channel / memory / plugin 扩展保持代码优先，不额外引入 manifest
- 聚合统计优先使用有界表或按日分片，不再依赖单个无限增长的 `events.jsonl`

## 开发文档

- 架构与边界约束：[DEVELOPMENT.md](DEVELOPMENT.md)
- 部署与运维说明：[DEPLOYMENT.md](DEPLOYMENT.md)
