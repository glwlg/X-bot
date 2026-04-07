# Ikaros 多平台部署教程

这份文档覆盖三类部署目标：

1. `Ikaros Core`：运行 `src/main.py`，负责 Telegram / Discord / 钉钉 / 微信等 Bot 通道。
2. `Ikaros API`：运行 FastAPI + Web SPA，默认监听 `8764` 端口。
3. `Wispaper`：文末给出与语音转写接口对接的部署说明。

当前仓库已经补齐了可直接使用的部署脚本，全部位于 `scripts/`：

| 脚本 | 用途 |
|---|---|
| `scripts/deploy_wizard.sh` | Linux / macOS 交互式部署向导：初始化仓库根 `.env` / `~/.ikaros/config/models.json`、可选完整设置 Primary / Routing 的 provider、baseUrl、apiKey 与模型绑定，并按选择自动部署 Core / API |
| `scripts/deploy_wizard_macos.sh` | macOS 一键部署入口，内部复用 `deploy_wizard.sh` 并限制只在 macOS 运行 |
| `scripts/deploy_wizard.ps1` | Windows PowerShell 一键部署向导：初始化仓库根 `.env` / `~/.ikaros/config/models.json`、可选完整设置 Primary / Routing，并按选择自动部署 Core / API |
| `scripts/build_web.sh` | 构建 Web 前端到 `src/api/static/dist` |
| `scripts/run_api.sh` | 非 Docker 方式启动 API |
| `scripts/deploy_api_compose.sh` | 用 `docker-compose.yml` 部署 API |
| `scripts/install_systemd_service.sh` | Linux systemd 服务安装，默认使用 `--user`，也支持 `--system` |
| `scripts/install_launchd_service.sh` | macOS launchd 服务安装 |
| `scripts/build_web.ps1` | Windows 构建 Web 前端 |
| `scripts/run_api.ps1` | Windows 非 Docker 启动 API |
| `scripts/run_ikaros.ps1` | Windows 启动 Core |
| `scripts/install_windows_task.ps1` | Windows 计划任务方式常驻运行 |

## 1. 通用准备

### 1.0 一键向导（推荐）

如果你是第一次部署，或者不想手动拼装多个脚本，建议优先使用一键向导：

```bash
./scripts/deploy_wizard.sh
```

macOS 也可以直接使用平台入口：

```bash
./scripts/deploy_wizard_macos.sh
```

Windows PowerShell 使用：

```powershell
.\scripts\deploy_wizard.ps1
```

它会交互式完成这些事情：

- 自动补齐缺失的 `.env` 和 `~/.ikaros/config/models.json`
- 让你选择 `Ikaros Core` 和 `Ikaros API` 各自的部署方式
- 可选直接写入 `Primary` / `Routing` 的 provider、`baseUrl`、`apiKey`、模型绑定；也可以留到 Web 配置页再配置
- 按你的选择自动执行 `uv sync`、前端构建和部署
- 最后直接提示你打开 Web 地址完成首个管理员初始化，并进入 `/admin/runtime` / `/admin/models`

说明：

- `deploy_wizard.sh` 面向 Linux / macOS Bash 环境
- `deploy_wizard_macos.sh` 是 macOS 包装入口
- `deploy_wizard.ps1` 面向 Windows PowerShell
- 如果 API 选的是宿主机脚本 / systemd / launchd，向导会自动先构建 Web
- 如果 API 选的是 Docker Compose，镜像构建阶段会自动打包 Web
- 渠道凭证、`ADMIN_USER_IDS`、`SOUL.MD`、`USER.md` 仍建议在 Web 的运行配置页完成

### 1.1 运行环境

- Python：`3.14+`
- `uv`
- Node.js：建议 `22.x`，至少 `20+`
- Docker / Docker Compose：仅 API 的 Docker 部署需要

### 1.2 初始配置准备

在仓库根目录执行：

```bash
cp .env.example .env
mkdir -p ~/.ikaros/config
cp config/models.example.json ~/.ikaros/config/models.json
```

至少建议确认这些配置：

- `MODELS_CONFIG_PATH`
- `ADMIN_USER_IDS`
- `SEARXNG_URL`
- `TELEGRAM_BOT_TOKEN`
- `DISCORD_BOT_TOKEN`
- `DINGTALK_CLIENT_ID`
- `DINGTALK_CLIENT_SECRET`
- `WEIXIN_BASE_URL`
- `WEIXIN_CDN_BASE_URL`

如果只部署 API，不跑 Bot 通道，可以暂时不填写各平台 Token。

### 1.3 安装 Python 依赖

```bash
uv sync
```

### 1.4 构建前端

Linux / macOS:

```bash
./scripts/build_web.sh --install
```

Windows PowerShell:

```powershell
.\scripts\build_web.ps1 -Install
```

构建产物会输出到 `src/api/static/dist/`，由 FastAPI 直接托管。

## 2. Linux 部署

### 2.1 API 使用 `docker-compose.yml` 部署

这是当前仓库默认推荐的 API 部署方式。

启动：

```bash
./scripts/deploy_api_compose.sh up
```

查看日志：

```bash
./scripts/deploy_api_compose.sh logs
```

查看状态：

```bash
./scripts/deploy_api_compose.sh ps
```

停止：

```bash
./scripts/deploy_api_compose.sh down
```

说明：

- API 容器名称是 `ikaros-api`
- compose 内使用 `network_mode: "host"`，默认暴露 `8764`
- 容器会挂载 `${HOME}/.ikaros/data` 和 `${HOME}/.ikaros/config`
- 前端静态资源由 Dockerfile 在镜像构建阶段自动打包

### 2.2 API 不使用 Docker 部署

如果你不想用 Docker，直接在宿主机启动即可。

先确保依赖安装完成：

```bash
uv sync
./scripts/build_web.sh --install
```

启动 API：

```bash
./scripts/run_api.sh --host 0.0.0.0 --port 8764
```

开发调试可加热重载：

```bash
./scripts/run_api.sh --reload
```

说明：

- `run_api.sh` 会默认再次执行一次前端构建
- 如果你已经手动构建过，可加 `--skip-build`
- 本地非 Docker 模式下，脚本会自动补 `PYTHONPATH=src`，避免 `uvicorn api.main:app` 找不到模块

### 2.3 Core 使用 systemd 用户服务常驻（推荐）

先确认直接运行没问题：

```bash
./scripts/run_ikaros.sh
```

安装 systemd 服务：

```bash
./scripts/install_systemd_service.sh --user --service-name ikaros --runner scripts/run_ikaros.sh
```

查看状态：

```bash
systemctl --user status ikaros
journalctl --user -u ikaros -f
```

如果你明确需要机器级 service，再改用：

```bash
./scripts/install_systemd_service.sh --system --service-name ikaros --runner scripts/run_ikaros.sh
```

### 2.4 API 使用 systemd 用户服务常驻（推荐）

如果 API 也不想放到 Docker，可以直接把 `run_api.sh` 注册成 systemd 服务：

```bash
./scripts/install_systemd_service.sh --user --service-name ikaros-api --runner scripts/run_api.sh
```

查看状态：

```bash
systemctl --user status ikaros-api
journalctl --user -u ikaros-api -f
```

说明：

- `--user` 适合单用户 / 自托管部署，也是当前默认值
- 如果希望用户未登录前也能在开机后自动拉起，可执行 `sudo loginctl enable-linger <user>`
- 如果你明确需要机器级 service，再改用 `--system`

## 3. macOS 部署

macOS 推荐使用宿主机直接运行，服务化采用 `launchd`。

如果你想直接走交互式一键部署，优先使用：

```bash
./scripts/deploy_wizard_macos.sh
```

### 3.1 初始化

```bash
cp .env.example .env
mkdir -p ~/.ikaros/config
cp config/models.example.json ~/.ikaros/config/models.json
uv sync
./scripts/build_web.sh --install
```

### 3.2 启动 API

```bash
./scripts/run_api.sh --host 0.0.0.0 --port 8764
```

### 3.3 使用 launchd 常驻 API

```bash
./scripts/install_launchd_service.sh --label com.ikaros.api --runner scripts/run_api.sh
```

查看状态：

```bash
launchctl print gui/$(id -u)/com.ikaros.api
```

日志默认落在：

```bash
~/.ikaros/data/logs/com.ikaros.api.out.log
~/.ikaros/data/logs/com.ikaros.api.err.log
```

### 3.4 使用 launchd 常驻 Core

```bash
./scripts/install_launchd_service.sh --label com.ikaros.core --runner scripts/run_ikaros.sh
```

## 4. Windows 部署

Windows 推荐用 PowerShell + 计划任务常驻。

如果你想直接走交互式一键部署，优先使用：

```powershell
.\scripts\deploy_wizard.ps1
```

### 4.1 初始化

在 PowerShell 中执行：

```powershell
Copy-Item .env.example .env
Copy-Item config\models.example.json config\models.json
uv sync
.\scripts\build_web.ps1 -Install
```

### 4.2 启动 API

```powershell
.\scripts\run_api.ps1 -Host 0.0.0.0 -Port 8764
```

如果只是重启 API 进程，不想再次构建前端：

```powershell
.\scripts\run_api.ps1 -SkipBuild
```

### 4.3 启动 Core

```powershell
.\scripts\run_ikaros.ps1
```

### 4.4 注册 Windows 常驻任务

注册 API：

```powershell
.\scripts\install_windows_task.ps1 -TaskName IkarosApi -Runner scripts/run_api.ps1
```

注册 Core：

```powershell
.\scripts\install_windows_task.ps1 -TaskName IkarosCore -Runner scripts/run_ikaros.ps1
```

查询状态：

```powershell
Get-ScheduledTask -TaskName IkarosApi | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName IkarosCore | Get-ScheduledTaskInfo
```

如果要在开机阶段而不是登录阶段启动，可增加：

```powershell
.\scripts\install_windows_task.ps1 -TaskName IkarosApi -Runner scripts/run_api.ps1 -TriggerMode Startup
```

`Startup` 模式通常需要管理员权限，因为默认会以 `SYSTEM` 运行。

## 5. 反向代理与端口建议

- API 默认端口：`8764`
- 如果要对公网开放，建议放到 Nginx / Caddy / Traefik 后面
- 生产环境建议只暴露反向代理端口，不直接暴露 Python 进程
- `~/.ikaros/data`、`~/.ikaros/config` 和仓库根 `.env` 必须持久化备份

## 6. 部署自检清单

### 6.1 API 自检

访问：

```text
http://<server>:8764/
```

如果前端已正确构建，应该能看到 Web 界面而不是 404。

### 6.2 Core 自检

- 进程启动后会初始化数据库、scheduler、extension runtime
- 已配置的平台会开始连接各自 adapter
- 如果某个平台没启用，对应 token 留空即可

### 6.3 常见问题

1. `uvicorn api.main:app` 找不到模块  
   原因：宿主机直接运行时没有把 `src/` 放进导入路径。  
   处理：使用 `scripts/run_api.sh` 或 `scripts/run_api.ps1`。

2. 服务环境里找不到 `uv` / `node`  
   原因：systemd / launchd / 计划任务不会继承交互 shell 的 PATH。  
   处理：优先把 `uv`、`node` 放到稳定路径；Linux 额外参考 `scripts/SERVICE_TOOL_SYMLINKS.md`。

3. API 页面能开但静态资源 404  
   原因：前端没构建到 `src/api/static/dist`。  
   处理：执行 `scripts/build_web.sh --install` 或 `scripts/build_web.ps1 -Install`。

## 7. Wispaper 部署

这一节按“你的 `wispaper` 实际提供的是一个 Whisper HTTP 转写接口”来写。  
Ikaros 当前读取的配置键是：

- `VIDEO_TO_TEXT_WHISPER_ENDPOINT`
- `WHISPER_INFERENCE_URL`

只要 `wispaper` 最终能暴露一个可访问的 HTTP 转写入口，就可以接入 Ikaros。

### 7.1 最低接入要求

`wispaper` 需要满足下面几点：

1. 提供 HTTP 接口，例如 `http://127.0.0.1:20800/inference`
2. 支持 `multipart/form-data` 上传音频文件
3. 能返回纯文本或 JSON 转写结果
4. 建议与 Ikaros 部署在同机或同内网，减少音频上传耗时

### 7.2 Ikaros 侧配置

在 `.env` 中增加：

```dotenv
VIDEO_TO_TEXT_WHISPER_ENDPOINT="http://127.0.0.1:20800/inference"
VIDEO_TO_TEXT_WHISPER_LANGUAGE="zh"
VIDEO_TO_TEXT_WHISPER_RESPONSE_FORMAT="json"
VIDEO_TO_TEXT_WHISPER_TIMEOUT_SECONDS="180"
VIDEO_TO_TEXT_WHISPER_TEMPERATURE="0"
VIDEO_TO_TEXT_WHISPER_TEMPERATURE_INC="0.2"
VIDEO_TO_TEXT_WHISPER_NO_TIMESTAMPS="1"
```

如果你更习惯旧键名，也可以只配：

```dotenv
WHISPER_INFERENCE_URL="http://127.0.0.1:20800/inference"
```

### 7.3 部署建议

- `wispaper` 如果已经有官方 compose / systemd / launchd 方案，优先沿用它自己的官方方式
- 如果它只提供裸进程启动方式，也没问题，核心是把最终可访问的接口地址填到 `.env`
- 如果要公网访问，建议给 `wispaper` 加反向代理和鉴权，不要直接裸露推理端口
- 音频转写接口建议单独监控超时和磁盘空间

### 7.4 验证方式

部署完成后，确认：

1. `curl http://127.0.0.1:20800/inference` 至少能连通到服务
2. Ikaros 启动后不再提示 `VIDEO_TO_TEXT_WHISPER_ENDPOINT is not configured`
3. 上传语音或视频时，日志里可以看到 Whisper HTTP 转写链路被启用

如果你的 `wispaper` 不是 Whisper HTTP 服务，而是另一个具体产品，只需要把这一节里的访问地址和它要求的部署方式替换掉，但 Ikaros 侧最终仍然是对接 `VIDEO_TO_TEXT_WHISPER_ENDPOINT`。
