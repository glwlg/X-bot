# 第二阶段重构计划：权限过滤 + Skill 恢复 + 管理工具 Skill 化

## 背景

第一阶段已完成 Skill 与 Tool 的基础解耦（[load_skill](file:///home/luwei/workspace/x-bot/src/core/skill_loader.py#229-235) 原子工具 + SOP Markdown），但存在以下遗留问题：
1. 技能目录未按角色权限过滤，Manager 看到了全部技能
2. `stock_watch` 和 `download_video` 的 SKILL.md 不当——让 Agent 凭空写代码，丢失了原有的服务层脚本
3. `list_workers / dispatch_worker / worker_status / software_delivery` 仍以 Function Call 直接注入，应转为 Skill

---

## 变更 1：技能目录按角色权限过滤

### [MODIFY] [prompt_composer.py](file:///home/luwei/workspace/x-bot/src/core/prompt_composer.py)

[_build_skill_catalog()](file:///home/luwei/workspace/x-bot/src/core/prompt_composer.py#106-136) 需要接受 `runtime_user_id` 和 `platform` 参数，对每个 skill 做 `tool_access_store.is_tool_allowed()` 检查，只展示当前角色有权使用的技能。

- Manager 的 allow 列表只包含 `group:management, group:automation, group:coding, group:primitives, group:skill-admin`
- Worker 的 allow 列表是 `group:all` 减去 deny，所以大部分技能对 Worker 可见

> [!IMPORTANT]
> 这意味着 Manager 将看不到 `stock_watch`, `download_video` 等技能，符合预期——这些操作应该由 Manager 派发给 Worker 执行。

---

## 变更 2：恢复 stock_watch 脚本 + 修复 SKILL.md

### [RESTORE] skills/builtin/stock_watch/scripts/

从 git 恢复 [scripts/execute.py](file:///home/luwei/workspace/x-bot/skills/builtin/docker_ops/scripts/execute.py) 和 `scripts/services/` 目录（包含 `stock_service.py`）。

### [MODIFY] [SKILL.md](file:///home/luwei/workspace/x-bot/skills/builtin/stock_watch/SKILL.md)

重写 SKILL.md：
- 说明 [scripts/execute.py](file:///home/luwei/workspace/x-bot/skills/builtin/docker_ops/scripts/execute.py) 提供了完整的股票服务（增删自选股、行情查询等）
- 明确用户数据存储位置由代码内部管理（数据库），**禁止** Agent 自行选择存放路径
- 告知使用方式：通过 [bash](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#136-160) 调用 `python scripts/execute.py <子命令> <参数>`
- 列出支持的子命令及参数格式

---

## 变更 3：恢复 download_video 脚本 + 修复 SKILL.md

### [RESTORE] skills/builtin/download_video/scripts/

从 git 恢复 [scripts/execute.py](file:///home/luwei/workspace/x-bot/skills/builtin/docker_ops/scripts/execute.py) 和 `scripts/services/download_service.py`。

### [MODIFY] [SKILL.md](file:///home/luwei/workspace/x-bot/skills/builtin/download_video/SKILL.md)

重写 SKILL.md：
- 说明 [scripts/execute.py](file:///home/luwei/workspace/x-bot/skills/builtin/docker_ops/scripts/execute.py) 提供了完整的下载能力，包括路径管理
- **明确写明下载文件的存放路径**（由 `download_service.py` 内部控制）
- 告知使用方式：通过 [bash](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#136-160) 调用 `python scripts/execute.py <url> [options]`
- 包括视频/音频格式选择参数

---

## 变更 4：管理工具从 Function Call 转为 Skill

### 核心思路

`list_workers / dispatch_worker / worker_status / software_delivery` 目前是直接作为 Function Call schema 注入给 LLM 的。按照新架构，它们应该和其他 Skill 一样：
1. **移除** `tool_registry.get_manager_tools()` 的注入
2. **创建** 对应的 Skill.md，作为 SOP 文档
3. **保留** [ToolCallDispatcher](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#73-547) 中的执行逻辑不变——LLM 通过 [load_skill](file:///home/luwei/workspace/x-bot/src/core/skill_loader.py#229-235) 获取 SOP，然后通过 tool name 调用
4. **关键区别**：这些工具的 schema 不再预先注入，而是在 [load_skill](file:///home/luwei/workspace/x-bot/src/core/skill_loader.py#229-235) 后 LLM 知道应该调用它们，由 [ToolCallDispatcher](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#73-547) 识别并执行

> [!IMPORTANT]
> 由于这些工具并非 bash 命令，而是 Python 内部函数。LLM 调用时仍以 function call name 识别（如 [dispatch_worker](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#171-201)），但它们**不会出现在初始 tools 列表**。`ToolCallDispatcher.execute()` 中用 [available_tool_names](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#90-93) 做白名单检查，需要将这些工具名添加到白名单，即使它们不在 [assemble()](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#65-71) 返回列表中。

### [NEW] [skills/builtin/worker_management/SKILL.md](file:///home/luwei/workspace/x-bot/skills/builtin/worker_management/SKILL.md)

新建 Skill，包含 [list_workers](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#161-170)、[dispatch_worker](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#171-201)、[worker_status](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#202-221) 的使用说明：
- 设置 `allowed_roles: [manager]` 元数据，使其只对 Manager 可见
- SOP 说明何时需要派发、如何选择 Worker、如何查询状态

### [NEW] [skills/builtin/software_delivery/SKILL.md](file:///home/luwei/workspace/x-bot/skills/builtin/software_delivery/SKILL.md)

新建 Skill，包含 [software_delivery](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#222-339) 的使用说明：
- 设置 `allowed_roles: [manager]`
- SOP 说明各 action 的使用场景

### [MODIFY] [tool_registry.py](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py)

- 移除 [get_manager_tools()](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#15-22) 方法（或使其返回空列表）
- 保留 schema 定义方法（[_dispatch_worker_tool](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#171-201) 等）以供内部引用

### [MODIFY] [orchestrator_runtime_tools.py](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py)

- [assemble()](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#65-71) 中移除 `tool_registry.get_manager_tools()` 调用
- `ToolCallDispatcher.__init__` 中确保 [available_tool_names](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#90-93) 包含管理工具名称（即使不在 tools 列表中），使 [execute()](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#296-547) 认识它们
- 或者：改为在 [execute()](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#296-547) 中不再严格检查 [available_tool_names](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#90-93) 白名单，而是由 [_filter_by_policy](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#48-64) + [_runtime_tool_allowed](file:///home/luwei/workspace/x-bot/src/core/agent_orchestrator.py#625-657) 在执行时做权限判断

---

## 验证计划

### 自动验证
1. `docker compose up --build -d` 构建通过
2. 日志中 `final tools` 列表只包含 `read, write, edit, bash, load_skill`（不含管理工具）
3. 日志中 `Final Prompt` 的技能目录按角色正确过滤

### 手动验证
请用户向 bot 发消息验证：
- Manager 角色对话时只看到管理类技能和权限内的技能
- Worker 执行时可以看到操作类技能（stock_watch、download_video 等）
