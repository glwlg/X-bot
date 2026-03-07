# 记账功能全量实现 Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** 参照记账 App 截图，在 X-Bot Web 管理后台中实现完整的记账功能模块。大首页展示多个功能模块入口（当前仅记账），点击记账进入记账子系统（含 5 个子页面）。

**Architecture:**
- **大首页 `/home`**：功能模块入口卡片（记账模块、更多模块待扩展）
- **记账子系统 `/accounting/*`**：独立路由前缀，5 个子页面（首页/资产/统计/更多/我的），有自己的底部 Tab 导航或侧边栏子菜单
- 后端 API 路径保持 `/api/v1/accounting/*`
- 数据库所有记账表名统一使用 `accounting_` 前缀

**Tech Stack:** Python/FastAPI (后端), Vue 3/TypeScript/TailwindCSS/ECharts/Lucide Icons (前端), SQLite (数据库)

**参考截图 (7 张):**
- 截图1: 记账首页 — 账本选择、月支出趋势图、最近交易、月预算
- 截图2: 资产 — 净资产卡片、账户列表（现金/储蓄卡/信用卡分组）
- 截图3: 统计 — 分类统计饼图、年度统计柱状图、多人统计
- 截图4: 我的 — 用户信息、记账天数/交易笔数/净资产、分类/商家/标签管理、导入导出
- 截图5: 更多 — 往来管理（借入/借出）、计划管理（周期/分期/预算/存钱）
- 截图6: 快速记账 — 从首页 FAB 按钮弹出数字键盘
- 截图7: 记账详情 — 支出/收入/转账切换、分类选择、账户/备注/日期

---

## Task 1: 数据库表名迁移 — 统一 accounting_ 前缀

**目标:** 将所有记账相关的 4 张表名加上 `accounting_` 前缀。

**Files:**
- Modify: `src/api/models/accounting.py`

**Step 1: 修改表名和扩展 Account 模型**

将 `__tablename__` 全部改为 `accounting_` 前缀，同时为 Account 添加 `type` 和 `balance` 字段：

```python
from sqlalchemy import String, ForeignKey, Numeric, DateTime
from sqlalchemy.orm import mapped_column, Mapped, relationship
from datetime import datetime
from api.core.database import Base


class Book(Base):
    __tablename__ = "accounting_books"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class Account(Base):
    __tablename__ = "accounting_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(50), default="现金", nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)


class Category(Base):
    __tablename__ = "accounting_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_categories.id"), nullable=True
    )


class Record(Base):
    __tablename__ = "accounting_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_books.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_accounts.id"), nullable=True
    )
    target_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_accounts.id"), nullable=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_categories.id"), nullable=True
    )
    record_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    payee: Mapped[str] = mapped_column(String(100), nullable=True)
    remark: Mapped[str] = mapped_column(String(500), nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
```

**Step 2: 删除旧数据库文件（开发阶段）**

由于改了表名，需要重建数据库：

```bash
rm -f data/bot_data.db
```

> 注意：生产环境应使用 Alembic migration，开发阶段直接重建。

**Step 3: 提交**

```bash
git add src/api/models/accounting.py
git commit -m "refactor(db): rename accounting tables with accounting_ prefix"
```

---

## Task 2: 后端 API 扩展 — 统计与账户管理

**目标:** 为前端提供所有需要的数据查询接口。

**Files:**
- Rewrite: `src/api/api/accounting_router.py`

**Step 1: 完整重写 accounting_router.py**

新增以下端点（在已有的 books/records/import/csv 基础上）：

1. `POST /records` — 手动创建记录
2. `GET /records/summary` — 月度收支汇总 `{ income, expense, balance }`
3. `GET /records/daily-summary` — 按日汇总 `[{ date, income, expense }]`
4. `GET /records/category-summary` — 按分类汇总 `[{ category, amount }]`
5. `GET /records/yearly-summary` — 按年汇总 `[{ year, income, expense }]`
6. `GET /accounts` — 获取账户列表（按 type 分组）
7. `POST /accounts` — 创建账户
8. `PUT /accounts/{id}` — 更新账户
9. `GET /categories` — 获取分类列表
10. `GET /stats/overview` — 统计概览 `{ days, transactions, net_assets }`

**Step 2: 提交**

```bash
git add src/api/api/accounting_router.py
git commit -m "feat(accounting): add full CRUD and stats API endpoints"
```

---

## Task 3: 前端路由 & 侧边栏 & Store & API 层

**目标:** 搭建前端整体路由结构——大首页 + 记账子系统嵌套路由。

**Files:**
- Modify: `src/platforms/web/src/router/index.ts`
- Modify: `src/platforms/web/src/layouts/MainLayout.vue`
- Modify: `src/platforms/web/src/api/accounting.ts`
- Create: `src/platforms/web/src/stores/accounting.ts`
- Create: `src/platforms/web/src/layouts/AccountingLayout.vue`

**Step 1: 路由结构**

```
/home                → 大首页 (Dashboard)，展示功能模块入口卡片
/accounting          → redirect → /accounting/overview
/accounting/overview → 记账首页（月概览+交易列表）
/accounting/assets   → 资产管理
/accounting/stats    → 统计报表
/accounting/more     → 更多功能
/accounting/profile  → 个人中心
/login               → 登录（public）
```

**Step 2: 创建 AccountingLayout.vue**

记账子系统的布局容器，包含底部 Tab 导航栏（模仿 App 底部 5 个 Tab：首页/资产/统计/更多/我的）：

```vue
<template>
  <div class="flex flex-col h-full">
    <!-- 返回大首页 -->
    <div class="sticky top-0 z-10 bg-theme-elevated border-b border-theme-primary px-4 py-2">
      <router-link to="/home" class="text-sm text-indigo-500">← 返回首页</router-link>
    </div>
    <!-- 页面内容 -->
    <div class="flex-1 overflow-auto">
      <RouterView />
    </div>
    <!-- 底部 Tab -->
    <nav class="flex border-t border-theme-primary bg-theme-elevated">
      <router-link to="/accounting/overview" class="tab-item">首页</router-link>
      <router-link to="/accounting/assets" class="tab-item">资产</router-link>
      <router-link to="/accounting/stats" class="tab-item">统计</router-link>
      <router-link to="/accounting/more" class="tab-item">更多</router-link>
      <router-link to="/accounting/profile" class="tab-item">我的</router-link>
    </nav>
  </div>
</template>
```

**Step 3: 更新侧边栏**

MainLayout 侧边栏 `menuItems` 保持简洁：

```typescript
const menuItems = computed(() => [
    { path: '/home', label: '首页', icon: LayoutDashboard },
])
```

> 记账子页面通过底部 Tab 导航，不占用侧边栏。

**Step 4: 扩展前端 API 和 Store**

- `accounting.ts` 增加所有统计/CRUD API 函数
- `stores/accounting.ts` 管理当前 bookId，全局共享

**Step 5: 提交**

```bash
git add src/platforms/web/src/
git commit -m "feat(frontend): add dashboard + accounting sub-routing with tab layout"
```

---

## Task 4: 大首页 (Dashboard) — 功能模块入口

**目标:** 实现大首页，展示功能模块卡片入口。

**Files:**
- Rewrite: `src/platforms/web/src/views/Home/HomeView.vue`

**Step 1: 重写 HomeView 为 Dashboard**

大首页设计：
1. **欢迎区**：欢迎回来 + 用户名
2. **功能模块卡片网格**：
   - **智能记账**（可点击，跳转 `/accounting`）：大卡片，显示图标 + 名称 + 简述 + 本月支出/收入概览数字
   - **更多模块**（灰色占位）：RSS 订阅、定时任务、心跳监控等——显示"即将推出"

**Step 2: 提交**

```bash
git add src/platforms/web/src/views/Home/
git commit -m "feat(frontend): implement dashboard home with module entry cards"
```

---

## Task 5: 记账首页 (AccountingOverview) — 月概览 + 趋势图 + 最近交易 + 快速记账

**目标:** 实现截图1和截图6/7的内容。

**Files:**
- Create: `src/platforms/web/src/views/Accounting/OverviewView.vue`
- Create: `src/platforms/web/src/components/accounting/AddRecordDialog.vue`

**Step 1: 实现 OverviewView**

参照截图1：
1. **顶部**：账本选择下拉 + 搜索图标
2. **月支出卡片**：当月支出总额 + 环比变化 + 结余 + 趋势折线图（ECharts）
3. **最近交易列表**：最近 5 条交易
4. **月预算卡片**：环形进度图
5. **FAB 按钮**：浮动 "+" 按钮，弹出记账弹窗

**Step 2: 创建 AddRecordDialog**

参照截图6和7：
1. 支出/收入/转账 Tab
2. 金额显示区
3. 分类选择网格（餐饮/购物/日用等 12 个分类）
4. 账户选择、备注输入、日期选择
5. 数字键盘
6. 保存按钮

**Step 3: 提交**

```bash
git add src/platforms/web/src/views/Accounting/ src/platforms/web/src/components/accounting/
git commit -m "feat(frontend): implement accounting overview with chart, transactions, and add record dialog"
```

---

## Task 6: 资产页面 (AssetsView) + 统计页面 (StatsView)

**目标:** 实现截图2和截图3的内容。

**Files:**
- Create: `src/platforms/web/src/views/Accounting/AssetsView.vue`
- Create: `src/platforms/web/src/views/Accounting/StatsView.vue`

**Step 1: AssetsView（截图2）**

1. **净资产卡片**：渐变背景 + 净资产/资产/负债 + 趋势图
2. **账户分组列表**：按 type 分组（现金/储蓄卡/信用卡），每个账户一行

**Step 2: StatsView（截图3）**

1. **分类统计**：环形饼图 + 分类列表
2. **年度统计**：柱状图
3. **多人统计**：环形饼图

**Step 3: 提交**

```bash
git add src/platforms/web/src/views/Accounting/
git commit -m "feat(frontend): implement assets and stats pages with ECharts"
```

---

## Task 7: 更多页面 (MoreView) + 个人中心 (ProfileView)

**目标:** 实现截图4和截图5的内容。

**Files:**
- Create: `src/platforms/web/src/views/Accounting/MoreView.vue`
- Create: `src/platforms/web/src/views/Accounting/ProfileView.vue`

**Step 1: MoreView（截图5）**

功能入口卡片网格（2×2）：
- 往来管理：借入/借出/报销/往来
- 计划管理：周期/分期/预算/存钱
- 均为占位 UI，暂无后端数据

**Step 2: ProfileView（截图4）**

1. 用户信息卡片（头像/用户名/ID + 记账天数/交易笔数/净资产）
2. 功能管理网格（分类/项目/商家/标签/导入/导出/共享/账本）
3. 设置列表（自动记账/组件/日志）

**Step 3: 提交**

```bash
git add src/platforms/web/src/views/Accounting/
git commit -m "feat(frontend): implement more and profile pages"
```

---

## Task 8: 构建、部署、验证

**目标:** 编译通过、容器部署成功、页面可正常访问。

**Step 1: 构建并部署**

```bash
docker compose down && docker compose up --build -d
```

**Step 2: 验证容器状态**

```bash
docker compose ps
docker compose logs x-bot-api --tail=20
```

**Step 3: 验证页面**

浏览器访问 `http://192.168.1.100:8764`：
1. `/home` — 大首页，功能模块卡片
2. 点击"智能记账"卡片 → `/accounting/overview`
3. 底部 Tab 切换：资产 / 统计 / 更多 / 我的
4. FAB → 记账弹窗 → 输入金额 → 选分类 → 保存

**Step 4: 提交**

```bash
git add .
git commit -m "feat: complete accounting module with dashboard home"
```
