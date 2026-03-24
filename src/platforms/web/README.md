# Ikaros Web 前端

基于 **Vue 3 + Vite + TailwindCSS 4** 构建的 Ikaros Web 管理面板，提供记账、RSS 订阅、定时任务、心跳监控等核心功能的可视化管理界面。

## 技术栈

- ⚡️ **Vite** — 极速冷启动和热重载
- 🎨 **TailwindCSS 4** — 原子 CSS 样式方案
- 🧩 **Lucide Icons** — 统一的图标库
- 🔒 **JWT 鉴权** — 内置 Token 生命周期管理与路由守卫
- 🌙 **深色模式** — 原生主题适配，支持跟随系统 / 手动切换
- 📱 **移动端优先** — 针对手机浏览器优化的全屏交互

## 功能模块

### 🏠 首页 (HomeView)
- 欢迎面板，展示当前登录用户信息
- 模块卡片导航（记账、RSS、定时任务、心跳监控）
- **平台账号绑定**：支持绑定 / 解绑 Telegram、Discord 等平台用户 ID，绑定后可在 Web 端查看和管理 Bot 平台数据
- 深色模式切换、退出登录

### 💰 智能记账 (Accounting)
独立的记账模块，使用 `AccountingLayout` 底部导航栏布局，包含以下子页面：

| 页面 | 路由 | 功能 |
|------|------|------|
| 总览 | `/accounting/overview` | 月度收支概览、快速记账入口 |
| 资产 | `/accounting/assets` | 账户资产管理 |
| 账户详情 | `/accounting/account/:id` | 单个账户的交易明细 |
| 统计 | `/accounting/stats` | 收支趋势、分类占比等多维统计 |
| 分类统计 | `/accounting/stats/category` | 按分类维度的详细统计 |
| 年度趋势 | `/accounting/stats/trend` | 年度收支趋势图 |
| 多人统计 | `/accounting/stats/team` | 多用户协作统计 |
| 统计面板管理 | `/accounting/stats/panels` | 自定义统计面板的增删改 |
| 编辑统计 | `/accounting/stats/panels/edit/:id?` | 创建 / 编辑统计面板配置 |
| 更多 | `/accounting/more` | 扩展功能入口 |
| 我的 | `/accounting/profile` | 个人信息与偏好 |
| 管理中心 | `/accounting/manage/:kind` | 分类 / 账户 / 项目管理 |
| 设置 | `/accounting/settings/:kind` | 各类设置项 |
| 交易明细 | `/accounting/records` | 历史交易列表与筛选 |
| 交易详情 | `/accounting/records/:id` | 单笔交易的完整信息 |
| 预算管理 | `/accounting/budgets` | 月度预算设定与跟踪 |
| 往来管理 | `/accounting/debts` | 借贷与应收应付管理 |
| 计划管理 | `/accounting/scheduled-tasks` | 周期性记账计划 |

### 📡 RSS 订阅 (RssView)
- 路由：`/modules/rss`
- 查看当前订阅列表
- **新增** / **编辑** / **删除**订阅
- 需先在首页绑定平台账号才能查看 Bot 端数据

### ⏰ 定时任务 (SchedulerView)
- 路由：`/modules/scheduler`
- 查看所有定时任务，显示 Crontab 表达式和指令内容
- **新增** / **编辑** / **删除**任务
- 支持**启用 / 停用**任务（开关切换）
- 需先在首页绑定平台账号

### 💓 心跳监控 (MonitorView)
- 路由：`/modules/monitor`
- 查看监控清单项
- **新增** / **编辑** / **删除**监控项
- 需先在首页绑定平台账号

### � 自选股 (WatchlistView)
- 路由：`/modules/watchlist`
- 管理自选股列表，展示股票代码和名称
- **新增** / **编辑** / **删除**自选股
- 需先在首页绑定平台账号

### �🔐 登录 (LoginView)
- 路由：`/login`
- 邮箱 + 密码登录
- JWT Token 持久化

## 目录结构

```
src/
├── api/                # API 请求封装
│   ├── request.ts      # Axios 实例（baseURL、拦截器、Token 注入）
│   ├── accounting.ts   # 记账相关接口
│   ├── auth.ts         # 认证接口
│   └── ...
├── components/
│   ├── ui/             # 通用 UI 基础组件
│   └── ThemeToggle/    # 深色模式切换组件
├── layouts/
│   ├── MainLayout.vue      # 全局主布局
│   └── AccountingLayout.vue # 记账模块底部导航布局
├── router/
│   └── index.ts        # 路由配置 + 前置鉴权守卫
├── stores/
│   ├── auth.ts         # 用户认证状态
│   ├── accounting.ts   # 记账业务状态
│   ├── theme.ts        # 主题状态
│   └── ...
├── views/
│   ├── Home/           # 首页（含平台绑定 UI）
│   ├── Auth/           # 登录页
│   ├── Modules/        # 功能模块（RSS / 定时任务 / 监控）
│   └── Accounting/     # 记账模块（18 个子页面）
└── utils/              # 工具函数
```

## 后端 API 依赖

| 前缀 | 用途 |
|------|------|
| `/api/v1/auth` | 用户认证（登录、注册、Token 刷新） |
| `/api/v1/binding` | 平台账号绑定（查询、绑定、解绑） |
| `/api/v1/rss` | RSS 订阅 CRUD |
| `/api/v1/scheduler` | 定时任务 CRUD + 启停 |
| `/api/v1/monitor` | 心跳监控清单 CRUD |
| `/api/v1/watchlist` | 自选股 CRUD |
| `/api/v1/accounting` | 记账全部接口 |

## 快速开始

1. **安装依赖**（推荐 Node.js 20+）
```bash
npm install
```

2. **启动开发服务器**
```bash
npm run dev
```

3. **构建生产包**
```bash
npm run build
```
构建产物输出至 `../../api/static/dist/`，由后端 FastAPI 静态文件服务提供。

## 注意事项

- 构建时 `vue-tsc` 会做类型检查，新增页面需确保 TS 类型完整
- RSS / 定时任务 / 心跳监控三个模块需要先在首页绑定 Telegram 用户 ID 后才能查看数据
- 所有 API 请求通过 `src/api/request.ts` 统一发出，自动注入 Bearer Token
