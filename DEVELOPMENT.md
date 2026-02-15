# X-Bot DEVELOPMENT

更新时间：2026-02-15  
状态：`TODO-ONLY`（本文定义目标架构、目录结构与开发待办，不代表已全部实现）

## 1. 设计目标

X-Bot 采用“双层操作系统”模型：

- 内核层（Core Manager）负责调度、治理、记忆与系统修复。
- Worker 执行层（Worker Fleet）负责面向用户的任务执行与交付。
- Core Manager 默认不执行用户业务任务，只做调度者、管理者、维护者。
- 当进入“修复/治理模式”时，Core Manager 可以对 worker 与工具链进行修复操作。

## 2. 双层架构（目标态）

### 2.1 内核层（Core Manager）

职责：

- 路由：接收用户输入并决定是直接对话、派发任务、还是进入治理流程。
- 编排：维护任务状态机、重试预算、超时、失败归因与回执。
- 治理：管理 worker 生命周期、权限、工具白名单与人格配置。
- 修复：基于 worker 失败上下文进行诊断与修复（仅限治理范围）。
- 记忆：维护短期上下文与长期记忆（自我记忆 / 用户记忆）。
- 维护：驱动 heartbeat 周期任务（健康检查、经验压缩、记忆固化）。

非职责：

- 不直接承担常规用户任务执行。
- 不把“编码工具执行”当成默认路径。

### 2.2 Worker 执行层（Worker Fleet）

职责：

- 执行任务：接受 Core Manager 派发，完成用户目标并返回结果。
- 工具使用：按调度策略调用原语与扩展工具。
- 失败回报：执行失败时回传结构化错误与诊断线索给 Core Manager。
- 运行隔离：每个 worker 有独立工作区、独立运行态、独立 SOUL。

非职责：

- 不修改 Core Manager 内核逻辑。
- 不越权管理其他 worker。

## 3. 项目目录结构（保留）

以下为需要长期保留并持续维护的结构说明（目录名可扩展，但职责边界应保持）：

### 3.1 仓库顶层

```text
.
├── src/                    # 主代码
├── skills/                 # 技能（builtin / learned）
├── data/                   # 运行态与持久化数据
├── tests/                  # 测试
├── docs/                   # 设计与说明文档
├── docker-compose.yml      # 多容器编排
├── pyproject.toml          # Python 项目配置
└── DEVELOPMENT.md          # 本开发文档
```

### 3.2 `src/` 结构

```text
src/
├── main.py
├── agents/
├── core/
│   ├── agent_orchestrator.py
│   ├── worker_runtime.py
│   ├── worker_store.py
│   ├── heartbeat_store.py
│   ├── heartbeat_worker.py
│   ├── tool_registry.py
│   └── config.py
├── handlers/
│   ├── ai_handlers.py
│   ├── worker_handlers.py
│   ├── heartbeat_handlers.py
│   ├── voice_handler.py
│   └── document_handler.py
├── services/
├── repositories/
├── platforms/
└── mcp_client/
```

### 3.3 `skills/` 结构

```text
skills/
├── builtin/                # 系统内置技能
└── learned/                # 学习/安装得到的技能
```

### 3.4 `data/` 关键运行态

```text
data/
├── WORKERS.json            # worker 元数据
├── WORKER_TASKS.jsonl      # worker 任务事件流水
├── userland/workers/       # 各 worker 工作区
├── users/                  # 用户维度状态与文件
├── runtime_tasks/          # 运行期任务上下文缓存
└── credentials/workers/    # worker 凭证（如 CLI auth）
```

说明：

- `/worker tasks` 的主要数据源是 `data/WORKER_TASKS.jsonl`。
- `data/userland/workers/<worker_id>/` 为空通常表示该任务未产出文件型副作用（例如 `echo hello`）。
- 任务事件标准字段：`source` / `status` / `created_at` / `started_at` / `ended_at` / `error` / `retry_count` / `events[]`。
- `/worker tasks` 查询链路：`handlers/worker_handlers.py -> core.worker_store.WorkerTaskStore.list_recent -> data/WORKER_TASKS.jsonl`。
- heartbeat 运行态查询链路：`handlers/heartbeat_handlers.py -> core.heartbeat_store.get_state -> data/runtime_tasks/<user_id>/{HEARTBEAT.md,STATUS.json}`。
- 对话检索链路：`/chatlog -> repositories.chat_repo.search_messages -> chat_history(SQLite)`。

## 4. 任务调度模型（目标态）

### 4.1 默认调度原则

- 普通聊天请求默认可自动派发到默认 worker（无需先输入 `/worker`）。
- `/worker` 命令用于显式控制和运维，不应成为唯一任务入口。
- Core Manager 负责派发、追踪和治理；Worker 负责执行与回执。

### 4.2 状态机

- `queued -> running -> (done | failed | cancelled)`
- 每个状态变更必须持久化、可追溯。
- 任务来源必须标注：`user_chat` / `user_cmd` / `heartbeat` / `system`。

### 4.3 已识别调度缺口

- 普通聊天在“派发-执行-回传”链路上仍有体感延迟。
- `/worker tasks` 默认视图被 heartbeat 失败记录噪声影响。
- 部分路径仍对编码工具可用性耦合过深，影响简单任务直达执行。
- `runtime_tasks` 目录层级出现异常膨胀，需做 key 规范与清理策略。

## 5. 工具调度策略（目标态）

### 5.1 调用优先级

先原语，后扩展：

1. `read`
2. `write/edit`
3. `bash`
4. `browser/search`
5. 编码工具（`codex` / `gemini-cli`）

原则：四原语可解时，不升级到编码工具。

### 5.2 失败恢复链

- 第 1 段：同工具自修复重试（参数、输入、环境）。
- 第 2 段：降级到四原语兜底。
- 第 3 段：启用备选工具或备选执行路径。
- 仅在 `fatal` 或恢复预算耗尽时失败交付。

## 6. SOUL.MD 人格系统（目标态）

每个执行体（包括 Core Manager）都必须持有独立 `SOUL.MD`，其内容注入系统提示词。

### 6.1 角色约束

- Core Manager：具备名字、性格、治理风格与长期自我记忆。
- Worker：执行型人格，强调任务交付，不持有独立长期人生记忆。
- SOUL 支持版本化、审计、回滚。

### 6.2 建议路径

- `data/kernel/core-manager/SOUL.MD`
- `data/userland/workers/<worker_id>/SOUL.MD`

## 7. 记忆系统（目标态）

Core Manager 采用双层记忆：

### 7.1 短期记忆（Short-Term Context）

- 会话窗口、当前任务、最近决策依据。
- 与任务生命周期绑定，可快速淘汰。

### 7.2 长期记忆（Long-Term Memory）

- 自我记忆：系统经验、修复模式、策略偏好、历史教训。
- 用户记忆：用户偏好、长期要求、显式“请记住”事项。

### 7.3 对话留存与压缩

- 与用户的对话必须全量留存。
- heartbeat 周期性压缩提炼：
  - 系统经验（写入自我记忆）
  - 用户偏好（写入用户记忆）
- 记忆条目必须保留来源引用与时间戳。

## 8. Heartbeat 职责（目标态）

- 心跳是周期维护机制，不是即时任务队列。
- 用于健康检查、对话压缩、记忆固化与治理提醒。
- 无事项时返回 `HEARTBEAT_OK` 并抑制主动打扰。
- 支持 `every + active_hours + pause/resume`。

## 9. 开发待办（按优先级）

### P0

- [x] `ARCH-001`：落地“Core Manager 默认只调度；修复模式可执行治理任务”。
- [x] `ARCH-002`：普通聊天默认自动派发到默认 worker。
- [x] `ARCH-003`：统一术语为“Worker 执行层（Worker Fleet）”，清理旧“用户层”命名。
- [x] `SCHED-001`：缩短派发链路时延，解决“延迟后才有结果”的体验。
- [x] `SCHED-002`：任务事件流标准化（来源、状态、时间、错误、重试）。
- [x] `TOOLS-001`：实现“先四原语，后外部工具”的强制选路。
- [x] `TOOLS-002`：简单 shell 任务不依赖 codex/gemini-cli 可用性。
- [x] `SOUL-001`：实现 per-agent `SOUL.MD` 加载、注入、版本化。
- [x] `MEM-001`：实现 Core Manager 双层记忆（短期 + 长期）。
- [x] `MEM-002`：长期记忆拆分“自我记忆 / 用户记忆”。
- [x] `LOG-001`：实现全量对话留存与可检索。
- [x] `HB-001`：heartbeat 驱动对话压缩与记忆写入。

### P1

- [x] `OBS-001`：补齐任务存储位置与查询链路的可观测说明。
- [x] `OBS-002`：`/worker tasks` 增加过滤（仅 `user_chat` / 排除 heartbeat）。
- [x] `HB-002`：heartbeat 结果分级（OK / NOTICE / ACTION）。
- [x] `SAFE-001`：核心配置、SOUL、记忆文件支持审计与回滚。
- [x] `CLEAN-001`：治理 `runtime_tasks` 异常层级膨胀。

### P2

- [x] `MEM-003`：用户记忆写入支持“显式确认 + 自动候选”双通道。
- [x] `MEM-004`：经验去重、冲突合并、置信度评分。
- [x] `TOOLS-003`：工具能力画像（成本/时延/成功率）驱动智能选路。

## 10. 验收标准（针对本规划）

- 用户不使用 `/worker` 也能触发默认 worker 执行。
- Core Manager 不执行普通业务任务，但可执行修复/治理任务。
- 简单命令（如 `echo hello`）不受 codex/gemini-cli 可用性影响。
- 每个 agent 可加载独立 `SOUL.MD` 并在提示链路生效。
- 长期记忆能区分“系统经验”和“用户偏好”。
- 对话全量留存，heartbeat 可周期提炼经验与偏好。

## 11. 备注

- 本文是开发任务规范，不等同于已上线能力。
- 实施时请在 PR/提交中关联任务编号（如 `ARCH-001`）。

## 12. 2026-02-15 交接状态（最新）

本节用于交接当前实现状态与遗留问题，供后续接手者快速定位。

### 12.1 本轮已落地（代码层）

- [x] Core Manager / Worker 分角色提示词与 SOUL 文件加载链路已接入。
- [x] Worker 运行态禁用 memory 工具（策略硬边界）。
- [x] memory 调用统一走 `src/mcp_client/memory.py`（MCP）。
- [x] function call 工具注入增加策略过滤，不再只靠提示词约束。
- [x] Manager 派发后进度播报（10s）与最终回执链路已打通。
- [x] Manager 派发给 Worker 时可封装近期对话上下文，并按判定附带记忆摘要。
- [x] 用户记忆读取增加 `open_nodes(User)` -> `read_graph` 兜底与旧数据自修复（补建 `User` 实体）。

### 12.2 线上表现与预期偏差（未闭环）

- [ ] `OPEN-001`：任务派发后，Worker 仍可能返回“缺失地点/上下文”，即使用户记忆已存在地点信息。
- [ ] `OPEN-002`：Manager 人设在部分轮次仍退化为通用“AI 助手”话术，SOUL 风格稳定性不足。
- [ ] `OPEN-003`：`task / chat / memory` 分流仍有不稳定，部分本应 Manager 直答的问题被派发到 Worker。
- [ ] `OPEN-004`：Manager 对 Worker 返回结果的二次治理不足，未稳定执行“缺信息时先补上下文再二次派发”。
- [ ] `OPEN-005`：当前“是否附带记忆摘要”依赖模型判定，仍存在误判与漂移风险。

### 12.3 复现样例（来自实际对话）

- [ ] `CASE-001`：用户问“明天天气怎么样”时已派发 Worker，但结果仍返回“缺失所在城市/时区”。
- [ ] `CASE-002`：用户问“你是谁”时，回复偏厂商模板身份，未稳定体现 Core Manager SOUL。
- [x] `CASE-003`：用户问“我是谁”可检索到“江苏无锡 / 偏好称呼：主人”（该链路当前可用）。

### 12.4 建议接手优先级（给下一位实现者）

1. [ ] 先做“Manager 回执治理闭环”：当 Worker 缺信息且 Manager 已有记忆时，自动二次派发而非直接返回缺失信息。
2. [ ] 将“是否需要上下文/记忆”从单次模型判定升级为可观测策略（含判定日志与可回放）。
3. [ ] 对 `你是谁` 这类身份问题建立通用身份策略测试（禁止问句硬编码），保证 SOUL 稳定生效。
4. [ ] 为天气/本地化/推荐类任务增加端到端集成测试（含 memory 命中、派发、回执）。

### 12.5 关键代码入口（交接索引）

- `src/handlers/ai_handlers.py`：派发、上下文封装、memory 读写、回执主链路。
- `src/core/prompt_composer.py`：角色与 SOUL 注入。
- `src/core/prompts.py`：默认系统提示词与通用约束。
- `src/core/tool_access_store.py`：工具分组与 worker memory 禁用策略。
- `src/core/agent_orchestrator.py`：function call 工具注入过滤。
- `src/mcp_client/memory.py`：MCP memory 实现（保留方案）。
