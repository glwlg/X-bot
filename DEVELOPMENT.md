# 💻 X-Bot 开发手册

本文档专为开发者设计，详细说明了 X-Bot 的系统架构、文件结构以及功能扩展指南。

## 1. 系统架构

X-Bot 采用模块化分层设计，基于 `python-telegram-bot` 和异步 I/O 构建。

```mermaid
graph TD
    User([👤 User]) <-->|Telegram API| Bot([🤖 X-Bot Server])

    subgraph "X-Bot Core (Docker Container)"
        Dispatcher[📨 Dispatcher & Router]
        
        subgraph "Handlers Layer (src/handlers/)"
            StartH[🏁 Start Handlers]
            MediaH[📹 Media Handlers]
            AIH[🧠 AI Handlers]
            ReminderH[⏰ Reminder Handlers]
            SubH[📢 Subscription Handlers]
            StockH[📈 Stock Handlers]
            AdminH[🛡️ Admin Handlers]
        end
        
        subgraph "Services Layer (src/services/)"
            Intent[🧠 Intent Router]
            Downloader[📥 Download Service]
            WebSum[🕸️ Web Summary Service]
            StockSvc[📊 Stock Service]
            AISvc[✨ AI Service]
        end
        
        subgraph "Core Layer (src/core/)"
            Config[⚙️ Config]
            Scheduler[⏰ Scheduler]
            Prompts[📝 Prompts]
        end
        
        subgraph "Repository Layer (src/repositories/)"
            DB[(🗄️ SQLite Repositories)]
        end
        
        subgraph "Data (data/)"
            Downloads[📁 Downloads]
        end

        Dispatcher --> Intent
        Intent -->|Route Intent| Handlers Layer
        
        Handlers Layer --> Services Layer
        Services Layer --> Repository Layer
        Repository Layer --> DB
        MediaH --> Downloader --> Downloads
    end

    subgraph "External Services"
        Gemini([✨ Google Gemini])
        Platforms([🌐 Video Platforms])
    end

    AISvc <--> Gemini
    Downloader <--> Platforms
```

---

## 2. 核心模块说明

项目的核心代码位于 `src/` 目录下：

### 🗂️ 目录结构 (`src/`)

```
src/
├── main.py                     # 入口文件
├── core/                       # 核心配置与调度
│   ├── config.py               # 配置中心（环境变量、API Key）
│   ├── prompts.py              # 系统提示词
│   └── scheduler.py            # 定时任务管理
├── handlers/                   # 消息处理器
│   ├── base_handlers.py        # 基础工具（权限检查）
│   ├── start_handlers.py       # /start, /help, 主菜单
│   ├── ai_handlers.py          # AI 对话、图片/视频分析
│   ├── media_handlers.py       # 视频下载
│   ├── reminder_handlers.py    # 提醒功能
│   ├── subscription_handlers.py # RSS 订阅/监控
│   ├── feature_handlers.py     # 需求收集
│   ├── stock_handlers.py       # 自选股
│   ├── voice_handler.py        # 语音处理
│   ├── document_handler.py     # 文档处理
│   ├── admin_handlers.py       # 管理员命令
│   └── mcp_handlers.py         # MCP 工具调用
├── services/                   # 业务服务层
│   ├── ai_service.py           # Gemini AI 交互
│   ├── intent_router.py        # 自然语言意图路由
│   ├── download_service.py     # yt-dlp 视频下载
│   ├── web_summary_service.py  # 网页抓取与摘要
│   └── stock_service.py        # 股票行情服务
├── repositories/               # 数据访问层
│   ├── base.py                 # 数据库连接与初始化
│   ├── cache_repo.py           # 视频缓存
│   ├── user_stats_repo.py      # 用户统计
│   ├── reminder_repo.py        # 提醒任务
│   ├── subscription_repo.py    # RSS 订阅
│   ├── user_settings_repo.py   # 用户设置
│   ├── allowed_users_repo.py   # 白名单
│   └── watchlist_repo.py       # 自选股
├── mcp_client/                 # MCP 客户端模块
│   ├── base.py                 # MCP 服务抽象基类
│   ├── manager.py              # MCP 服务管理器
│   ├── memory.py               # 长期记忆服务
│   └── playwright.py           # Playwright 浏览器自动化
├── stats.py                    # 统计模块
├── utils.py                    # 通用工具函数
└── user_context.py             # 用户对话上下文
```

---

### 🏛️ 分层架构

| 层级 | 目录 | 职责 |
| :--- | :--- | :--- |
| **Handlers** | `handlers/` | 接收 Telegram 消息，调用 Services 处理业务 |
| **Services** | `services/` | 封装业务逻辑，与外部 API 交互 |
| **Repositories** | `repositories/` | 数据持久化，所有数据库操作 |
| **Core** | `core/` | 配置、调度、提示词等基础设施 |

---

### 🌐 MCP (Model Context Protocol) 扩展

MCP 模块允许 X-Bot 调用外部 MCP 服务。

#### 当前支持的 MCP 服务
 
 | 服务类型 | 功能 | 运行方式 |
 | :--- | :--- | :--- |
 | `playwright` | 网页截图、导航、交互 | Docker |
 | `memory` | 长期记忆 (Knowledge Graph) | Local npx |

---

## 3. 开发指引

### 🛠️ 环境搭建

推荐使用 [uv](https://github.com/astral-sh/uv) 进行 Python 依赖管理。

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
uv sync

# 本地运行
cp .env.example .env  # 填入 API Key
uv run src/main.py
```

### 🧪 运行测试

```bash
uv run pytest tests/ -v
```

---

### 📝 如何添加新功能？

#### 场景 A: 添加一个新的命令 (e.g., `/weather`)

1. 在 `src/handlers/` 下创建 `weather_handlers.py`
2. 在 `src/main.py` 中注册 `CommandHandler("weather", weather_command)`
3. 添加权限检查 `if not await check_permission(update): return`

#### 场景 B: 扩展自然语言路由 (e.g., "帮我查天气")

1. **修改意图枚举**: 在 `src/services/intent_router.py` 的 `UserIntent` 中添加 `CHECK_WEATHER`
2. **添加路由分发**: 在 `src/handlers/ai_handlers.py` 的 `handle_ai_chat` 中添加处理分支

#### 场景 C: 添加新的数据存储

1. 在 `src/repositories/` 下创建 `weather_repo.py`
2. 在 `repositories/__init__.py` 中导出新函数
3. 在 Handler 中 `from repositories import save_weather_data`

---

## 4. 注意事项

1. **异步编程**: 所有 I/O 操作 **必须** 使用 `await`
2. **错误处理**: 严禁未捕获异常，使用 `try...except` 并记录日志
3. **权限控制**: 敏感操作必须检查 `check_permission`
4. **数据库变更**: 修改表结构需更新 `repositories/base.py` 的 `init_db`
5. **CallbackQuery**: 新增回调前缀需更新 `main.py` 的 `common_pattern` 正则

---

Happy Coding! 👩‍💻👨‍💻
