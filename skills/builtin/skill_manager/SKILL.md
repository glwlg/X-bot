---
name: skill_manager
description: |
  核心技能中心。你必须通过此技能来管理所有能力。
  
  **核心能力**:
  1. **安装/搜索**: `install`, `search` - 获取新能力。
  2. **配置/定时**: `config` 或 `schedule` - 添加定时任务到数据库。
     - **查看任务**: `tasks` - (IMPORTANT) 列出所有正在运行的定时任务/Cron。
     - **删除任务**: `delete_task` - 删除指定任务。
  3. **进化/重构**: `modify` - 使用 AI 修改技能代码或逻辑 (需要审核)。
  4. **管理**: `list` - (IMPORTANT) 仅列出安装的 *技能包*，不列出运行的任务。
  5. **管理**: `delete` - 删除技能包。
  6. **创造**: `create` - 只有当搜索不到现有技能时，才使用此功能创建新技能。

triggers:
- search_skill
- install_skill
- create_skill
- delete_skill
- list_skills
- config_skill
- schedule_skill
- list_tasks
- delete_task
- modify_skill
- refactor_skill
- 进化技能
- 修改技能
- 重构技能
- 自动运行
- 周期执行
- 每天
- 每小时
- 定时任务
- cron
---

# Skill Manager & 技能系统规范

这个技能不仅是管理工具，也是 X-Bot 技能系统的**核心定义文档**。

## 1. 技能管理功能

用户可以通过自然语言或指令管理技能：

- **搜索技能**: "搜索天气技能" (搜索 GitHub)
- **配置/定时**: "每天早上8点运行 Moltbook" (Bot 会自动调用 `config` 接口修改配置)
- **查看任务**: "列出定时任务" 或 "Show scheduled tasks" -> 使用 `action="tasks"` (不要使用 `list`)
- **创建技能**: "创建一个查快递的技能" (AI 自动编程)
- **安装技能**: "安装 user/repo"
- **列出技能**: "列出已安装技能" -> 使用 `action="list"`
- **删除技能**: "删除 [技能名]"
- **删除任务**: "删除任务 1" -> 使用 `action="delete_task", task_id="1"`

## 2. 技能系统架构

### 2.1 标准技能结构 (Standard)

```
skills/learned/
  └── my_awesome_skill/
      ├── SKILL.md
      └── scripts/
          └── execute.py
```

#### SKILL.md 规范 (示例)

```markdown
---
name: my_awesome_skill
description: 这是一个示例技能描述。
triggers:
- 示例
- example
config: "value"
---
```
# 技能名称

这里是详细文档。当用户询问该技能用法时，Bot 会读取这部分内容。

## 使用方法
...
```

### 2.2 脚本规范 (scripts/execute.py)

如果技能需要执行逻辑（API 调用、数据处理），则需要 `scripts/execute.py`。

**基本模版:**

**基本模版:**

```python
from core.platform.models import UnifiedContext

# 必须包含 execute 函数
async def execute(ctx: UnifiedContext, params: dict) -> str:
    """
    params: 包含了从用户指令中提取的参数
    """
    user_id = ctx.message.user.id
    
    # 业务逻辑...
    
    # 必须返回一个字符串，向 Agent 汇报执行结果
    return "执行成功，结果是..." 
```

**可用能力 & 安全规则:**

- **网络**: 允许使用 `httpx` (推荐) 或 `subprocess` 调用 `curl`.
- **依赖**: 尽量使用标准库。项目已预装 `httpx`, `beautifulsoup4`, `yt-dlp` 等常用库。
- **文件**: 只能读写 `data/` 目录。
- **系统**: 禁止使用 `os.system` (请用 `subprocess`)，禁止高危操作。

## 3. 如何导入/分享技能？(Direct Adoption)

X-Bot 支持**零门槛导入**。只需将符合规范的 `SKILL.md` 文件链接发给 Bot 即可。

1. **编写**: 按上述规范编写 `SKILL.md` (如果需要代码，可以将代码内嵌在文档中，或者提供包含 `scripts` 的仓库).
2. **分享**: 将文件上传到 GitHub, Gist, Pastebin 或任何公开 URL.
3. **导入**: 发送链接给 Bot："看看这个技能 https://..." 或 "安装 https://..."
4. **生效**: Bot 会自动识别元数据，下载并请求您批准。

## 4. AI 自动进化

最强大的方式是让 Bot 自己写技能。

- "我需要一个能查币价的某些功能..." -> Bot 会自动编写代码 -> 生成 `SKILL.md` -> 请求审核 -> 存入 `skills/learned`。

---
*Power to the Agent. Code is capability.*

