# Pi 对齐核心改造方案（最终版，保留 MCP Memory）

## 1. 方案摘要

本方案将 X-Bot 核心改造成与 Pi 思路一致的架构：

1. 核心 Agent 默认只使用四原语工具：`Read` / `Write` / `Edit` / `Bash`。
2. Skill 作为扩展能力存在，默认不全量注入模型上下文，采用按需短时注入。
3. 只保留一个主决策循环，移除当前双层 LLM 决策链路（`AgentOrchestrator -> SkillAgent`）。
4. Skill 执行改为确定性执行器，不允许 Skill 内部再启动第二层 agent loop。
5. **记忆模块保持现状**：继续使用现有 MCP memory 方案，`src/mcp_client/memory.py` 不改，不引入 mem0。
6. 本 Bot 仅运行在 Docker 容器内，不考虑非容器部署路径。
7. 不保留回滚开关，不做双轨平滑迁移。
8. 新增任务驱动状态层：每用户由 `HEARTBEAT.md + STATUS.json` 持续跟踪，不依赖单个 skill 的内部流程。
9. 核心必须保持业务无关：禁止在核心与通用 skill 中硬编码特定组件仓库、镜像、端口等产品知识。

---

## 2. 已锁定决策

### 2.1 架构决策

1. 对齐程度：高度对齐 Pi。
2. Skill 执行模型：确定性执行器。
3. 扩展加载策略：按需短时注入。
4. 迁移策略：一次性切换，不保留 legacy/rollback 分支。
5. 部署边界：Docker-only。
6. 任务推进策略：围绕任务目标进行 loop（而非围绕某个 skill），由核心调度并完成交付。
7. 扩展容错策略：扩展是“插件层”，任何扩展异常都不能导致核心主循环崩溃。
8. 通用性约束：禁止针对测试样例（如具体组件）做硬编码优化。

### 2.2 记忆决策

1. 记忆模块先不改。
2. `src/mcp_client/memory.py` 保持原实现。
3. 现有 `MCP_MEMORY_ENABLED`、memory tool 注入链路保持可用。
4. 本次改造不引入 mem0、Qdrant、相关依赖和配置。

---

## 3. 现状基线（改造输入）

### 3.1 当前关键链路

1. 主入口：`src/core/agent_orchestrator.py`
2. 工具注册：`src/core/tool_registry.py`（当前核心是 `call_skill`）
3. 二级决策：`src/agents/skill_agent.py`
4. 模型循环：`src/services/ai_service.py`
5. Skill 加载：`src/core/skill_loader.py`
6. 记忆接入：`src/mcp_client/memory.py` + `src/core/agent_orchestrator.py`

### 3.2 当前主要问题

1. 默认工具哲学偏离 Pi：核心不是四原语，而是 `call_skill`。
2. 双层循环导致行为不稳定：主 loop + skill 子 loop。
3. 上下文控制信号与业务文本耦合（例如 `🔇🔇🔇` 语义混用）。
4. `skill_loader` 解析 `params` 但没有贯通到稳定 schema 执行。
5. 核心编排路径测试覆盖不足。

---

## 4. 目标架构（最终形态）

## 4.0 核心理念（必须长期遵守）

1. **核心是大脑，技能是外设**：核心负责理解目标、拆分步骤、调度能力、交付结果；技能只提供能力，不承载主任务 loop。
2. **任务中心，而非技能中心**：loop 应围绕“任务是否完成”推进，不围绕某个扩展是否返回文本。
3. **通用优先，禁用特化硬编码**：不得在核心与通用技能中写死组件仓库地址、镜像名、端口映射等业务特定知识。
4. **可观测且可恢复**：每用户必须有 `HEARTBEAT.md` 与 `STATUS.json`，便于卡死检测、自动恢复和人工诊断。
5. **隔离优先**：扩展失败要被核心吸收（超时、异常、输出畸形），不能把核心拖挂。

## 4.1 Core Agent Loop（唯一主循环）

文件：`src/core/agent_orchestrator.py`

职责：
1. 接收用户消息与历史。
2. 仅向模型暴露四原语工具。
3. 执行工具调用并回灌 observation。
4. 根据请求按需触发扩展执行（确定性，不再二次 LLM 决策）。
5. 产出最终回复并结束循环。

约束：
1. 不再从主路径调用 `SkillAgent`。
2. 不允许嵌套 agent loop。
3. 当工具/扩展失败时，核心 loop 应优先尝试修复与继续执行，而非立即向用户反复提问。

## 4.2 Primitive Runtime（四原语执行内核）

新增文件：`src/core/primitive_runtime.py`

提供能力：
1. `read(path, start_line, max_lines, encoding)`
2. `write(path, content, mode, create_parents)`
3. `edit(path, edits, dry_run)`
4. `bash(command, cwd, timeout_sec)`

统一返回：
1. 成功结构：`{"ok": true, "data": ..., "summary": "..."}`
2. 失败结构：`{"ok": false, "error_code": "...", "message": "..."}`

## 4.3 Extension Router（按需扩展路由）

新增文件：`src/core/extension_router.py`

职责：
1. 根据用户请求在已安装 skills 中做候选匹配。
2. 返回最多 1~3 个短时注入扩展（名称、描述、schema 摘要）。
3. 无匹配时返回空集合，不猜测。

## 4.4 Extension Executor（确定性扩展执行）

新增文件：`src/core/extension_executor.py`

职责：
1. 校验扩展输入参数。
2. 调用 Skill 标准入口（确定性）。
3. 汇总结果并回灌主 loop。
4. 对扩展执行施加隔离（超时、输出大小/文件数量限制、异常收敛）。

## 4.5 Task State Kernel（HEARTBEAT + STATUS）

文件：
1. `src/core/heartbeat_store.py`
2. `src/core/heartbeat_worker.py`
3. `src/core/task_manager.py`（heartbeat/task_id/active_task_id）

职责：
1. 为每个用户维护 `HEARTBEAT.md`（任务队列 + 事件）与 `STATUS.json`（锁 + 脉冲）。
2. 在核心循环关键阶段写入心跳（turn/tool/retry/final/block）。
3. 记录任务状态（pending/running/waiting_user/done/failed/cancelled/timed_out）与最近事件。
4. 为 watchdog/自动恢复提供统一状态源。
5. 采用保留策略限制文件体积：默认不长期保留 `done` 任务，仅保留活跃任务和有限终态记录。

## 4.6 Memory（保持原有 MCP 模块）

保持不变：
1. `src/mcp_client/memory.py`
2. `mcp_manager` 的 memory server 实例化机制
3. orchestrator 中 memory tools 注入逻辑（可在重构时做接口适配，但不替换后端）

---

## 5. 对外接口与类型变更

## 5.1 模型默认可见工具（替换现有默认 `call_skill`）

1. `read`
2. `write`
3. `edit`
4. `bash`

说明：
1. 扩展能力通过 orchestrator 内部逻辑按需触发，不作为默认常驻工具列表。
2. 记忆工具保持现有 MCP 注入策略（当 memory 开启时存在额外工具，这是本期明确保留项）。

## 5.2 Skill 协议升级（强制迁移）

建议升级到 `Skill Protocol v3`（一次性迁移）：
1. frontmatter 必填：`api_version`, `name`, `description`, `triggers`, `input_schema`, `permissions`, `entrypoint`
2. 执行签名统一：`execute(ctx, args, runtime)`
3. 不再允许无 schema 的模糊输入协议。
4. 不允许在 skill 内部实现新的 agent loop（skill 只做确定性执行）。
5. 不允许在通用 skill 中写死业务组件仓库/镜像/端口常量。

---

## 6. 文件级改造清单（执行顺序）

## Phase 1：搭四原语核心

1. 新增 `src/core/primitive_runtime.py`
2. 重写 `src/core/tool_registry.py`（默认导出四原语）
3. 新增 `src/core/types.py`（可选，放通用类型）

## Phase 2：收敛主 loop

1. 重写 `src/core/agent_orchestrator.py`
2. 保留 `src/services/ai_service.py`，补强 max-turn 触顶行为（必须返回可见失败信息）
3. 主路径移除 `SkillAgent` 调用
4. 引入工具失败后的自动重试引导（核心 loop 自恢复）

## Phase 3：扩展层改造

1. 新增 `src/core/extension_router.py`
2. 新增 `src/core/extension_executor.py`
3. 调整 `src/core/skill_loader.py` 以支持 v3 schema 校验与摘要输出
4. 增加扩展执行隔离（timeout/输出约束/异常收敛）

## Phase 3.5：任务状态内核

1. 新增 `src/core/heartbeat_store.py` 与 `src/core/heartbeat_worker.py`。
2. 扩展 `src/core/task_manager.py` 增加 heartbeat/task_id/active_task_id。
3. orchestrator 与 ai_service 事件接入 HEARTBEAT/STATUS 更新。

## Phase 4：Skill 全量迁移

迁移范围共 17 个 skill（builtin 15 + learned 2）：
1. `account_manager`
2. `deep_research`
3. `deployment_manager`
4. `docker_ops`
5. `download_video`
6. `file_manager`
7. `generate_image`
8. `notebooklm`
9. `reminder`
10. `rss_subscribe`
11. `scheduler_manager`
12. `searxng_search`
13. `skill_manager`
14. `stock_watch`
15. `web_browser`
16. `news_article_writer`
17. `xlsx`

统一动作：
1. `SKILL.md` 补齐 v3 必填字段。
2. `execute.py` 统一签名。
3. 文件/命令副作用操作改走 runtime。
4. 输出结构统一到 `ExtensionRunResult`。

## Phase 5：清理旧路径

1. 将 `src/agents/skill_agent.py` 从主调用路径剥离（可保留文件但不被 orchestrator 使用）。
2. 删除/替换与双循环相关的流程控制逻辑。
3. 清理 `call_skill` 作为默认核心能力的文案和提示词。

---

## 7. 记忆模块处理边界（本期强约束）

## 7.1 明确保留

1. `src/mcp_client/memory.py` 文件内容不改。
2. `MCP_MEMORY_ENABLED` 配置语义不改。
3. memory 工具获取和执行方式不改后端协议。

## 7.2 可调整（仅适配，不替换）

1. orchestrator 重构后对 memory 工具注入的调用位置可调整。
2. prompt 文案可微调，但不改 memory tool 名称体系（`open_nodes` / `create_entities` / `add_observations`）。

## 7.3 明确不做

1. 不引入 mem0。
2. 不新增 qdrant 容器。
3. 不修改 Dockerfile/pyproject 为 mem0 依赖。

---

## 8. 测试计划

## 8.1 新增测试文件

1. `tests/core/test_primitive_runtime.py`
2. `tests/core/test_tool_registry_pi_mode.py`
3. `tests/core/test_orchestrator_single_loop.py`
4. `tests/core/test_extension_router.py`
5. `tests/core/test_extension_executor.py`
6. `tests/core/test_ai_service_retry_loop.py`
7. `tests/core/test_task_manager_heartbeat.py`
8. `tests/core/test_heartbeat_store.py`
9. `tests/core/test_heartbeat_worker.py`

## 8.2 必测场景

1. 四原语闭环：Read -> Edit -> Write -> Read。
2. 扩展按需触发：命中 skill 后确定性执行成功。
3. 无扩展命中：主 loop 给出明确可执行建议。
4. 超时与取消：bash 长任务可中断。
5. max-turn 触顶：必须返回可见失败文本。
6. memory 开启时：memory tool 可用；关闭时不注入。
7. 工具失败后：核心 loop 自动继续尝试，不应直接退化为连续追问。
8. 每用户维护 `HEARTBEAT.md + STATUS.json`，并在关键阶段更新。
9. 扩展超时/异常时：核心仍可继续运行并向用户给出可见失败信息。

## 8.3 验收标准（DoD）

1. 默认工具仅四原语（除 memory 保留注入外）。
2. 主路径不再调用 `SkillAgent`。
3. 17 个 skill 迁移完成并可执行。
4. 核心编排新增测试通过。
5. Docker 容器内端到端可运行。
6. 核心与通用 skill 中无组件仓库/镜像/端口硬编码。
7. 任务态文件（HEARTBEAT + STATUS）可用于排障与恢复。

---

## 9. 风险与应对

1. 风险：一次性切换导致技能行为回归。
   应对：按 skill 风险分层迁移并逐层 smoke。

2. 风险：Skill 输出结构不统一导致编排器分支复杂。
   应对：强制统一 `ExtensionRunResult`。

3. 风险：Bash 宽松策略引入误执行。
   应对：保留最小安全底线（敏感文件/凭据泄露阻断）和审计日志。

4. 风险：memory 工具与四原语哲学混合。
   应对：明确将 memory 视为“保留系统能力”，本期不纳入替换范围。

5. 风险：为追求短期效果引入组件特化硬编码，破坏通用能力。
   应对：将“禁止组件硬编码”纳入 code review 与测试检查项。

---

## 10. 新会话直接执行 Runbook

1. 基于本文件创建改造分支：`refactor/pi-core-runtime`
2. 实现 `primitive_runtime + tool_registry`。
3. 重写 orchestrator 主 loop，移除 SkillAgent 主路径。
4. 实现 extension router/executor。
5. 迁移 17 个 skills 到确定性执行模型。
6. 接入 HEARTBEAT + STATUS 任务状态内核。
7. 补齐并通过核心测试。
8. 清理旧路径与文案。

---

## 11. 完成定义（Definition of Done）

1. 代码层：
1. `src/core/tool_registry.py` 默认只导出四原语。
2. `src/core/agent_orchestrator.py` 不再调用 `skill_agent.execute_skill`。
3. `src/agents/skill_agent.py` 不在主路径。
4. `src/mcp_client/memory.py` 保持不变。
5. `src/core/heartbeat_store.py`、`src/core/heartbeat_worker.py` 与 `src/core/task_manager.py` 提供任务可观测状态。

2. 行为层：
1. 用户常规任务由四原语完成。
2. 复杂能力通过按需扩展执行。
3. memory 行为与现状一致。
4. 扩展故障不导致核心不可用（核心仍可恢复/失败交付）。
5. 不依赖组件特化硬编码即可完成通用任务。

3. 质量层：
1. 新增核心测试通过。
2. Docker 内 smoke 与端到端通过。
