# 任务清单：第二阶段 Agent 架构重构

> 历史计划文档：其中涉及的 `worker_management` / `worker` 运行面设计基于旧架构，现已失效。

## 1. 🔧 技能目录按角色权限过滤
- [x] 修改 `prompt_composer._build_skill_catalog()` 接受 runtime_user_id/platform 参数
- [x] 对每个 skill 用 `tool_access_store.is_tool_allowed()` 做权限检查
- [x] 确保 Manager 只看到权限内的技能

## 2. 🔧 恢复 stock_watch 脚本 + 修复 SKILL.md
- [x] 从 git 恢复 [scripts/execute.py](file:///home/luwei/workspace/x-bot/skills/builtin/docker_ops/scripts/execute.py) 和 `scripts/services/`
- [x] 重写 SKILL.md，指引 Agent 使用已有脚本而非自行写代码

## 3. 🔧 恢复 download_video 脚本 + 修复 SKILL.md
- [x] 从 git 恢复 [scripts/execute.py](file:///home/luwei/workspace/x-bot/skills/builtin/docker_ops/scripts/execute.py) 和 `scripts/services/`
- [x] 重写 SKILL.md，明确写明存放路径和脚本调用方式

## 4. 🔧 管理工具从 Function Call 转为 Skill
- [x] 新建 `skills/builtin/worker_management/SKILL.md`
- [x] 新建 `skills/builtin/software_delivery/SKILL.md`
- [x] 从 [assemble()](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#65-71) 中移除 [get_manager_tools()](file:///home/luwei/workspace/x-bot/src/core/tool_registry.py#15-22)
- [x] 确保 [ToolCallDispatcher](file:///home/luwei/workspace/x-bot/src/core/orchestrator_runtime_tools.py#73-547) 仍能识别并执行这些工具
- [x] 将管理工具注册到 `SKILL_FUNCTION_GROUPS`，使权限系统正确归类

## 5. ✅ 验证
- [x] Docker build 通过
- [x] `final tools` 列表只含 read/write/edit/bash/load_skill
- [x] Manager 技能目录只显示权限内的技能
- [ ] 用户手动验证功能正常

## 6. 🔧 Skill CLI/SOP 统一化补充
- [x] 为其余 active skills 的 `scripts/execute.py` 补齐 CLI 入口
- [x] 将其余 active skills 的 `SKILL.md` 统一改成 bash/CLI SOP
- [x] 新增一致性测试，约束 `entrypoint: scripts/execute.py` 的 skill 必须具备 shell 权限和 CLI 主入口
- [x] 对补齐后的 skills 完成 `--help` 与轻量命令验证
