# X-Bot

X-Bot 是一个多平台 AI Bot，当前采用 `Manager + Worker + API` 三服务拆分架构。

- `x-bot`：Core Manager，负责对话入口、工具编排、技能加载、任务派发、进度回传，以及 manager 侧编码会话/仓库操作
- `x-bot-worker`：默认 Worker，负责异步任务执行、队列消费、结果落盘
- `x-bot-api`：FastAPI + SPA，提供 Web/API 能力

当前版本已经不是“单容器万能 bot”。代码、镜像、依赖和运行职责都按这三类服务拆开维护。

![logo](logo.png)

## 当前能力

- 多平台接入：Telegram、Discord、钉钉 Stream，以及独立 Web/API 服务
- 多模态交互：文本、图片、视频、语音、文档输入
- Manager/Worker 异步执行：普通任务可派发给 worker，结果和过程会回传到当前对话
- Skill 体系：技能放在 `skills/` 下，通过 `SKILL.md` 描述 SOP、参数契约、权限分组和可导出的 direct tool
- manager 直连编码链路：代码类任务优先组合使用 `repo_workspace`、`codex_session`、`git_ops`、`gh_cli`
- 文件系统优先状态：聊天、记忆、任务、权限、队列都持久化在 `data/`

## 架构概览

### 1. Core Manager

Manager 是当前系统的统一入口，负责：

- 接收平台消息和命令
- 组装提示词、SOUL、工具面和技能信息
- 按权限注入 `read/write/edit/bash/load_skill` 与 skill 导出的 direct tool
- 对普通异步任务做 worker 派发、跟踪和结果整合
- 对代码类任务直接编排 `repo_workspace` / `codex_session` / `git_ops` / `gh_cli`
- 把 manager 自身过程和 worker 过程回传给用户

### 2. Worker Kernel

Worker 从共享队列消费任务，执行默认 program，并把结果写回：

- 任务队列：`data/system/dispatch/tasks.jsonl`
- 结果队列：`data/system/dispatch/results.jsonl`
- Worker 注册表：`data/WORKERS.json`

### 3. Skills

Skill 是一等运行时扩展，位于：

- `skills/builtin/`
- `skills/learned/`

默认调用路径是：

1. `load_skill`
2. 模型读取 `SKILL.md`
3. 按 SOP 使用 `bash` 执行 `scripts/execute.py`

如果 skill 在 frontmatter 中声明了 `tool_exports`，它还可以被动态注入为 direct tool，而不需要再在核心代码里硬编码注册。

## 目录结构

```text
.
├── src/
│   ├── api/          # FastAPI + SPA
│   ├── core/         # 编排、提示词、工具装配、状态访问
│   ├── handlers/     # 命令和消息入口
│   ├── manager/      # 派发、relay、workspace/codex/git/gh 开发工具
│   ├── platforms/    # Telegram / Discord / DingTalk 适配层
│   ├── services/     # AI、下载、搜索等外部服务集成
│   ├── shared/       # manager/worker 共用协议与队列
│   └── worker/       # worker kernel 与 program runtime
├── skills/           # builtin + learned skills
├── data/             # 持久化状态
├── config/           # 部署与运行时配置
├── tests/            # pytest 测试
├── docker-compose.yml
├── README.md
└── DEVELOPMENT.md
```

## 快速开始

### 1. 准备配置

复制环境变量模板：

```bash
cp .env.example .env
cp config/models.example.json config/models.json
```

然后分两层配置：

1. `.env`：平台接入、运行时路径、调度和发布相关配置
2. `config/`：模型提供商、默认模型选择、部署目标等结构化配置

`.env` 里至少按需填写这些项目：

- `TELEGRAM_BOT_TOKEN`
- `DISCORD_BOT_TOKEN`
- `DINGTALK_CLIENT_ID`
- `DINGTALK_CLIENT_SECRET`
- `ADMIN_USER_IDS`
- `SEARXNG_URL`

如果要启用 Manager 的本地发布与 GitHub 链路，还需要：

- `X_DEPLOYMENT_STAGING_PATH`

`X_DEPLOYMENT_STAGING_PATH` 必须是宿主机绝对路径，并且容器内外路径一致。当前 Manager 是通过宿主机 Docker 做发布，不是容器内自建一层 Docker-in-Docker。

### 1.1 `config/` 配置说明

当前 `config/` 下有两个核心配置文件：

- `config/models.json`
- `config/models.example.json`
- `config/deployment_targets.yaml`

#### `config/models.json`

模型配置已经统一迁移到 `config/models.json`，不再通过环境变量选择模型或 provider。

仓库里默认提交的是示例文件 `config/models.example.json`。首次配置时请复制为 `config/models.json` 再修改：

```bash
cp config/models.example.json config/models.json
```

这个文件负责三件事：

1. 选择默认模型
   - `model.primary`
   - `model.routing`
   - `model.vision`
   - `model.image_generation`
   - `model.voice`
2. 定义模型池
   - `models.primary`
   - `models.routing`
   - `models.vision`
   - `models.image_generation`
3. 定义 provider 连接信息
   - `providers.<provider>.baseUrl`
   - `providers.<provider>.apiKey`
   - `providers.<provider>.api`
   - `providers.<provider>.models[]`

其中：

- `vision` 是看图/看视频/看表情包用的多模态理解模型
- `image_generation` 是文生图模型
- 旧字段 `image` 现在只作为 `vision` 的兼容别名，新的配置不要再用它表达生图模型

如果你要切换模型或接入新的 provider，应该改这里，而不是改 `.env`。

这个文件通常还会包含 provider 的密钥信息，不要把真实密钥提交到公开仓库。

如果想把模型配置文件放到别处，可以在 `.env` 中设置：

```bash
MODELS_CONFIG_PATH="/absolute/path/to/models.json"
```

#### `config/deployment_targets.yaml`

这个文件定义 manager 本地 rollout 对应的服务映射关系，例如：

- `manager -> x-bot / x-bot-manager`
- `worker -> x-bot-worker / x-bot-worker`
- `api -> x-bot-api / x-bot-api`

如果你的 compose service 名或镜像名变了，应该改这个文件，而不是改发布代码。

### 2. 安装依赖

本地开发：

```bash
uv sync
```

容器运行：

```bash
docker compose up --build -d
```

### 3. 启动方式

本地直接运行：

```bash
uv run python src/main.py
uv run python src/worker_main.py
uv run uvicorn api.main:app --host 0.0.0.0 --port 8764
```

用 `systemd` 托管宿主机 manager：

```bash
chmod +x scripts/run_manager.sh scripts/install_systemd_service.sh
./scripts/install_systemd_service.sh --system
```

如果你更想装成当前用户的 user service：

```bash
./scripts/install_systemd_service.sh --user
```

容器方式：

```bash
docker compose up --build -d
docker compose logs -f x-bot
docker compose logs -f x-bot-worker
docker compose logs -f x-bot-api
```

## 常用命令

当前默认注册的通用命令包括：

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

其中 `/teach` 现在只是过渡入口，会提示改用自然语言需求或新的 manager 编码工具链，不再走旧版“直接生成扩展代码”的实现。

## 运行时目录

- `data/`：状态、任务、权限、聊天记录、心跳、workspace/codex session 等运行时数据
- `downloads/`：媒体下载产物
- `skills/`：技能源码与 learned skills
- `config/`：结构化运行配置，当前主要包括 `models.json` 和 `deployment_targets.yaml`

## 进度回传

- Worker 进度会通过 manager relay 回传到原对话
- Manager 自身工具过程也会在对话内输出
- Telegram 上优先使用 `sendMessageDraft` 做单条草稿流式刷新，避免刷屏

## 镜像与依赖拆分

当前 `docker-compose.yml` 使用三个独立 target：

- `manager-runtime`
- `worker-runtime`
- `api-runtime`

Python 依赖也按角色拆分在 `pyproject.toml`：

- `manager`
- `worker`
- `api`
- `optional-skill-runtime`

这意味着后续可以只定向重建 `x-bot-worker` 或 `x-bot-api`，不再强制三者共用一张大镜像。

## 开发文档

- 架构与边界约束：[DEVELOPMENT.md](DEVELOPMENT.md)
- Web 搜索配置：[docs/web_search_config.md](docs/web_search_config.md)

## 当前维护原则

- 文档以当前实现为准，不再保留未落地愿景描述
- 代码类改动默认走 manager 原子工具链：`repo_workspace`、`codex_session`、`git_ops`、`gh_cli`
- 新增可直接暴露给模型的 skill tool，优先通过 `SKILL.md` 的 `tool_exports` 声明，而不是改核心硬编码
