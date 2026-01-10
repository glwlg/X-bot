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
            ServiceH[🛠️ Service Handlers]
            AdminH[🛡️ Admin Handlers]
        end
        
        subgraph "Logic Layer (src/)"
            Intent[🧠 Smart Router]
            Downloader[📥 yt-dlp Wrapper]
            WebSum[🕸️ Web Scraper]
            ImgGen[🎨 Image Gen]
            Scheduler[⏰ APScheduler]
        end
        
        subgraph "Data Layer (data/)"
            DB[(🗄️ SQLite Bot Data)]
            Downloads[file_folder Downloads]
        end

        Dispatcher --> Intent
        Intent -->|Check Intent| Handlers Layer
        
        StartH --> DB
        MediaH --> Downloader --> Downloads
        AIH --> WebSum
        AIH --> ImgGen
        ServiceH --> Scheduler --> DB
        AdminH --> DB
    end

    subgraph "External Services"
        Gemini([✨ Google Gemini Pro])
        Platforms([🌐 Video Platforms])
    end

    Intent <--> Gemini
    AIH <--> Gemini
    Downloader <--> Platforms
```

---

## 2. 核心模块说明

项目的核心代码位于 `src/` 目录下：

### 🗂️ 目录结构 (`src/`)

| 文件/目录 | 说明 |
| :--- | :--- |
| **`main.py`** | **入口文件**。负责初始化 Bot、加载环境变量、注册 Handlers、启动 Scheduler 和 Polling。 |
| **`config.py`** | **配置中心**。管理所有环境变量、API Key、以及各种全局常量配置。 |
| **`intent_router.py`** | **智能路由**。负责分析用户自然语言意图，分发给不同的 Handler (新增功能核心)。|
| **`database.py`** | **数据库层**。封装了 `aiosqlite`，提供用户白名单、上下文、统计、订阅等数据的增删改查。 |
| **`handlers/`** | **消息处理器包**。包含所有具体业务逻辑的 Handler。 |
| ├── `base_handlers.py` | 基础工具，如 `check_permission` 权限检查装饰器。 |
| ├── `start_handlers.py` | 处理 `/start`, `/help` 及主菜单回调。 |
| ├── `ai_handlers.py` | 处理文本对话、语音、图片/文档分析。**包含路由分发逻辑**。 |
| ├── `media_handlers.py` | 处理视频下载逻辑（解析 URL、调用 yt-dlp）。 |
| ├── `service_handlers.py` | 处理提醒、订阅、监控、统计等工具类服务。 |
| ├── `admin_handlers.py` | 处理 `/adduser`, `/deluser` 等管理员命令。 |
| **`downloader.py`** | 封装 `yt-dlp`，负责具体的视频下载和文件处理。 |
| **`web_summary.py`** | 网页抓取与摘要生成模块。 |
| **`scheduler.py`** | `APScheduler` 定时任务管理。 |

---

## 3. 开发指引

### 🛠️ 环境搭建

推荐使用 [uv](https://github.com/astral-sh/uv) 进行现代化的 Python 依赖管理。

1.  **安装 uv**:
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
2.  **安装依赖**:
    ```bash
    uv sync
    ```
3.  **本地运行**:
    *   复制 `.env.example` 为 `.env` 并填入 Key。
    *   运行：`uv run src/main.py`

### 📝 如何添加新功能？

#### 场景 A: 添加一个新的命令 (e.g., `/weather`)
1.  在 `src/handlers/service_handlers.py` 中编写 `weather_command` 函数。
2.  在 `src/main.py` 的 `main()` 函数中注册 `CommandHandler("weather", weather_command)`。
3.  不要忘记在函数开头添加 `if not await check_permission(update): return`。

#### 场景 B: 扩展自然语言路由 (e.g., "帮我查天气")
1.  **修改路由规则**: 打开 `src/intent_router.py`。
    *   在 `UserIntent` Enum 中添加 `CHECK_WEATHER`。
    *   在 `analyze_intent` 的 Prompt 中添加规则（触发词、参数提取）。
2.  **处理路由分发**: 打开 `src/handlers/ai_handlers.py`。
    *   在 `handle_ai_chat` 函数中找到 `Smart Intent Routing` 区域。
    *   添加 `elif intent == UserIntent.CHECK_WEATHER:` 分支，调用你的 `weather_command` 或相关逻辑。

---

## 4. 注意事项

1.  **异步编程**: 所有涉及 I/O (网络、数据库、文件) 的操作 **必须** 使用 `await`。
2.  **错误处理**: Bot 需要长期运行，**严禁** 在 Handler 中抛出未捕获异常导致进程崩溃。请使用 `try...except` 并记录 `logger.error`。
3.  **权限控制**: 任何敏感或消耗资源的操作，都必须先检查 `check_permission`。
4.  **数据库变更**: 如果修改了数据库结构，请确保 `database.py` 中的 `init_db` 能正确处理（目前项目较为简单，未引入类似 Alembic 的迁移工具，改表结构建议直接兼容或手动处理）。

---

Happy Coding! 👩‍💻👨‍💻
