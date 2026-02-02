---
name: skill_manager
description: |
  **核心技能中心**。负责管理所有能力（安装、搜索、创建、修改、删除）。
  
  **核心能力**:
  1. **安装/搜索**: `install`, `search` - 获取新能力。
  2. **进化/重构**: `modify` - 使用 AI 修改技能代码或逻辑 (需要审核)。
  3. **管理**: `list` - 列出安装的技能。
  4. **管理**: `delete` - 删除技能包。
  5. **创造**: `create` - 只有当搜索不到现有技能时，才使用此功能创建新技能。

triggers:
- search_skill
- install_skill
- create_skill
- delete_skill
- list_skills
- modify_skill
- refactor_skill
- 进化技能
- 修改技能
- 重构技能
- 搜索技能
- 安装技能
- 删除技能
---

# Skill Manager (技能中心)

你是一个负责管理 X-Bot 技能系统的核心助手。你的职责是帮助用户扩展 Bot 的能力边界。

## 核心能力

1.  **列出技能 (Action: list)**: 查看当前已安装的所有技能包 (Builtin + Learned).
2.  **搜索技能 (Action: search)**: 搜索可用技能 (优先返回**本地已安装**，其次搜索 **GitHub 市场**)。
3.  **安装技能 (Action: install)**: 从指定 URL 或仓库安装新技能。
4.  **删除技能 (Action: delete)**: 卸载并删除指定名称的技能。
5.  **创建技能 (Action: create)**: 根据需求描述，使用 AI 自动编写新技能。
6.  **修改技能 (Action: modify)**: 修改现有技能的代码逻辑或修复 Bug。
7.  **配置技能 (Action: config)**: (仅 metadata) 修改技能的 triggers, description 等元数据。

## 技能开发标准 (Skill Standard)

X-Bot 采用 **Skill-Centric** 架构，每个能力应尽量封装在独立的 Skill 包中。

### 1. 目录结构
```
skills/learned/<skill_name>/
├── SKILL.md          # 元数据 (YAML Frontmatter + 使用说明)
└── scripts/
    └── execute.py    # 核心逻辑与入口
```

### 2. SKILL.md 规范
```yaml
---
name: my_skill
description: 技能描述
triggers:        # 用于自然语言意图路由
  - 触发词1
  - 触发词2
params:          # (可选) 参数定义
  param1: string
---

# 使用说明
Markdown 格式的详细说明...
```

### 3. execute.py 规范

```python
from core.platform.models import UnifiedContext
from typing import Any

# 1. 核心执行入口 (必须)
async def execute(ctx: UnifiedContext, params: dict) -> str:
    """
    当通过 triggers 或 AI agent 调用时执行此函数
    """
    user_id = ctx.message.user.id
    # ... 业务逻辑 ...
    return "执行结果反馈"

# 2. 动态 Handler 注册 (可选)
def register_handlers(adapter_manager: Any):
    """
    注册特定的 Slash Command 或 Button Callback
    """
    # 注册命令 /my_cmd
    adapter_manager.on_command("my_cmd", my_custom_handler)
    
    # 注册回调 (Global)
    adapter_manager.on_callback_query(r"^my_skill:.*", my_callback_handler)

async def my_custom_handler(ctx: UnifiedContext):
    await ctx.reply("Hello from Custom Handler!")

async def my_callback_handler(ctx: UnifiedContext):
    await ctx.answer_callback()
    await ctx.reply(f"Clicked: {ctx.callback_data}")
```

## 执行指令 (SOP) -- (Internal)

当用户请求管理技能时，请分析其意图并提取以下参数调用内置脚本：

### 参数说明

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `action` | string | 是 | 操作类型: `list`, `search`, `install`, `delete`, `create`, `modify`, `config` |
| `skill_name` | string | 条件 | 目标技能名称 (create, delete, modify, config 时必填) |
| `query` | string | 条件 | 搜索关键词 (search 时必填) |
| `repo_url` | string | 条件 | 仓库地址 (install 时必填，格式 `owner/repo` 或 `https://...`) |
| `instruction` | string | 条件 | 给 AI 的具体指令 (create, modify 时必填，例如 "实现一个查天气的技能") |
| `key` | string | 条件 | 配置项键名 (config 时必填) |
| `value` | string | 条件 | 配置项新值 (config 时必填) |

### 意图映射示例

**1. 列出技能**
- 用户输入: "我有哪些技能？" / "查看已安装插件"
- 提取参数:
  ```json
  { "action": "list" }
  ```

**2. 搜索技能**
- 用户输入: "搜索一下有没有查汇率的技能"
- 提取参数:
  ```json
  { "action": "search", "query": "currency exchange" }
  ```

**3. 安装技能**
- 用户输入: "安装 glwlg/xbot-skills"
- 提取参数:
  ```json
  { "action": "install", "repo_url": "glwlg/xbot-skills" }
  ```

**4. 创建技能**
- 用户输入: "帮我写一个技能，可以查询 BTC 价格"
- 提取参数:
  ```json
  {
    "action": "create",
    "skill_name": "crypto_price",
    "instruction": "创建一个查询 BTC 价格的技能，使用 CoinGecko API"
  }
  ```

**5. 修改技能**
- 用户输入: "修改 weather 技能，增加显示湿度"
- 提取参数:
  ```json
  {
    "action": "modify",
    "skill_name": "weather",
    "instruction": "增加显示湿度的功能"
  }
  ```

**6. 删除技能**
- 用户输入: "删除 test 技能"
- 提取参数:
  ```json
  { "action": "delete", "skill_name": "test" }
  ```

## 注意事项

- **优先搜索**: 在创建新技能前，优先搜索已有的技能。
- **配置 vs 修改**: 
  - 如果用户只是想修改定时任务 (`crontab`) 或触发词 (`triggers`)，使用 `config`。
  - 如果用户想修改代码逻辑，使用 `modify`。
