# AI Native 重构实施计划

## 一、目标架构

### 1.1 核心原则
- **Manager / Worker 是同一套通用 Agent Loop**，只是 SOUL（角色设定）和工具权限不同
- **所有任务来源统一进入任务入口**（Task Inbox）：用户实时对话、Heartbeat、Cron、系统任务
- **Manager 负责决策**：是否自己做、是否派发 Worker、派发给哪个 Worker
- **Worker 只执行任务**：不知道任务来源，不做特判，执行完返回结构化结果
- **用户只看到 Manager 输出**：Worker 原始输出仅作为内部 observation
- **Worker 代码可运行时变更**（Skill / Workspace），Manager 代码只能发版变更

### 1.2 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        任务来源 (Sources)                          │
│   user_chat (实时对话) │ heartbeat │ cron │ system                │
└─────────────────────────────┬───────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    Task Inbox (统一任务入口)                        │
│   - task_id (UUID)                                                 │
│   - source: user_chat | heartbeat | cron | system                  │
│   - goal: 用户目标描述                                             │
│   - payload: 原始数据                                              │
│   - priority: high | normal | low                                  │
│   - user_id                                                        │
│   - requires_reply: bool                                           │
└─────────────────────────────┬───────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    Core Manager (LLM-driven)                        │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  LLM 决策循环：                                            │   │
│   │  1. 分析任务意图 (goal)                                     │   │
│   │  2. 决策：自己执行 vs 派发 Worker                          │   │
│   │  3. 如果派发：选择哪个 Worker                              │   │
│   │  4. 执行工具 / 派发任务                                    │   │
│   │  5. 等待 Worker 结果                                       │   │
│   │  6. 整合结果，统一输出给用户                                │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│   工具：                                                            │
│   - 四原语 (read/write/edit/bash)                                  │
│   - dispatch_worker(worker_id, instruction) - 新增                 │
│   - list_workers() - 新增                                          │
│   - await_worker(task_id) - 新增                                   │
│   - run_extension(skill_name, args) - 统一扩展入口                 │
└─────────────────────┬───────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    Worker Pool                                      │
│   ┌──────────────────┐   ┌──────────────────┐                   │
│   │   Worker-1       │   │   Worker-2       │                   │
│   │   (default)      │   │   (code-review)  │                   │
│   │   通用执行       │   │   专用执行       │                   │
│   └──────────────────┘   └──────────────────┘                   │
│                                                                     │
│   Worker 工具：                                                     │
│   - 四原语 (read/write/edit/bash)                                  │
│   - run_extension(skill_name, args)                                │
│   - 不做任务来源判断，不做特殊处理                                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、数据模型

### 2.1 Task Inbox Model

```python
# src/core/task_inbox.py (新建)

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

@dataclass
class TaskEnvelope:
    task_id: str                          # UUID
    source: str                            # user_chat | heartbeat | cron | system
    goal: str                              # 任务目标描述（自然语言）
    payload: Dict[str, Any]                # 原始数据
    priority: str                          # high | normal | low
    user_id: int                           # 用户 ID
    requires_reply: bool                   # 是否需要回复用户
    created_at: str                       # ISO timestamp
    status: str                           # pending | running | completed | failed
    
    # 调度相关
    assigned_worker_id: Optional[str] = None
    dispatch_reason: Optional[str] = None  # Manager 决策原因
    
    # 结果相关
    result: Optional[Dict[str, Any]] = None
    final_output: Optional[str] = None     # Manager 整合后的输出
```

---

## 三、文件改动清单

### 3.1 新建文件

| 文件路径 | 说明 |
|---------|------|
| `src/core/task_inbox.py` | 统一任务入口（Task Inbox + Task Store） |
| `src/core/tools/dispatch_tools.py` | Manager 调度工具实现（list_workers, dispatch_worker, await_worker） |
| `src/core/tools/extension_tools.py` | 统一扩展执行工具（run_extension） |

### 3.2 改动文件

| 文件路径 | 改动类型 | 说明 |
|---------|---------|------|
| `src/core/heartbeat_worker.py` | 大改 | 改为生成 Task Item，交给 Manager 决策 |
| `src/core/scheduler.py` | 大改 | Cron 任务写入 Task Inbox |
| `src/handlers/ai_handlers.py` | 大改 | 用户对话写入 Task Inbox，移除硬编码调度 |
| `src/core/agent_orchestrator.py` | 大改 | 集成 Task Inbox，添加工具执行器 |
| `src/core/worker_runtime.py` | 大改 | 简化为纯执行器，移除来源特判 |
| `src/core/tool_access_store.py` | 中改 | 移除来源特判逻辑 |
| `src/agents/skill_agent.py` | 删除 | 彻底移除（主路径） |
| `src/core/extension_executor.py` | 简化 | 改为统一扩展执行入口 |
| `src/core/prompts.py` | 新增 | Manager/Worker SOUL 提示词 |
| `src/core/soul_store.py` | 新增 | SOUL 动态加载逻辑 |

---

## 四、详细实施步骤

### Step 1: 创建 Task Inbox（任务入口）

**目标**：统一所有任务来源

**新建文件**: `src/core/task_inbox.py`

```python
"""
Task Inbox - 统一任务入口
所有任务（用户对话、heartbeat、cron）都写入这里
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

from core.config import DATA_DIR

logger = logging.getLogger(__name__)


@dataclass
class TaskEnvelope:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "system"  # user_chat | heartbeat | cron | system
    goal: str = ""          # 任务目标描述
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"  # high | normal | low
    user_id: int = 0
    requires_reply: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "pending"  # pending | running | completed | failed
    
    # 调度相关
    assigned_worker_id: Optional[str] = None
    dispatch_reason: Optional[str] = None
    
    # 结果相关
    result: Optional[Dict[str, Any]] = None
    final_output: Optional[str] = None
    
    # 元信息
    metadata: Dict[str, Any] = field(default_factory=dict)


class TaskInbox:
    """统一任务入口"""
    
    def __init__(self):
        self.root = Path(DATA_DIR) / "task_inbox"
        self.root.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, TaskEnvelope] = {}
        self._lock = asyncio.Lock()
    
    async def submit(
        self,
        source: str,
        goal: str,
        user_id: int,
        payload: Dict[str, Any] = None,
        priority: str = "normal",
        requires_reply: bool = True,
    ) -> TaskEnvelope:
        """提交一个新任务"""
        task = TaskEnvelope(
            source=source,
            goal=goal,
            user_id=user_id,
            payload=payload or {},
            priority=priority,
            requires_reply=requires_reply,
        )
        
        async with self._lock:
            self._tasks[task.task_id] = task
            await self._persist(task)
        
        logger.info(f"Task submitted: {task.task_id} source={source} goal={goal[:50]}")
        return task
    
    async def get(self, task_id: str) -> Optional[TaskEnvelope]:
        """获取任务"""
        async with self._lock:
            return self._tasks.get(task_id)
    
    async def update_status(self, task_id: str, status: str, **kwargs) -> bool:
        """更新任务状态"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.status = status
            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            await self._persist(task)
            return True
    
    async def assign_worker(self, task_id: str, worker_id: str, reason: str) -> bool:
        """分配 Worker"""
        return await self.update_status(
            task_id, "running", 
            assigned_worker_id=worker_id, 
            dispatch_reason=reason
        )
    
    async def complete(self, task_id: str, result: Dict[str, Any], final_output: str) -> bool:
        """完成任务"""
        return await self.update_status(
            task_id, "completed",
            result=result,
            final_output=final_output
        )
    
    async def fail(self, task_id: str, error: str) -> bool:
        """任务失败"""
        return await self.update_status(
            task_id, "failed",
            result={"error": error}
        )
    
    async def list_pending(self, limit: int = 100) -> List[TaskEnvelope]:
        """列出待处理任务"""
        async with self._lock:
            pending = [t for t in self._tasks.values() if t.status == "pending"]
            pending.sort(key=lambda x: (
                0 if x.priority == "high" else 1 if x.priority == "normal" else 2,
                x.created_at
            ))
            return pending[:limit]
    
    async def _persist(self, task: TaskEnvelope) -> None:
        """持久化任务到磁盘"""
        path = self.root / f"{task.task_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(task), f, ensure_ascii=False, indent=2)
    
    async def load_from_disk(self) -> None:
        """从磁盘加载任务"""
        async with self._lock:
            for path in self.root.glob("*.json"):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                        task = TaskEnvelope(**data)
                        self._tasks[task.task_id] = task
                except Exception as e:
                    logger.error(f"Failed to load task {path}: {e}")


task_inbox = TaskInbox()
```

---

### Step 2: 创建 Manager 调度工具

**目标**：让 Manager 有真实的工具可以调用，而不是只写在 prompt 里

**新建文件**: `src/core/tools/dispatch_tools.py`

```python
"""
Manager 调度工具 - 供 Agent Orchestrator 使用
"""

import logging
from typing import Any, Dict, List, Optional

from core.worker_store import worker_registry
from core.worker_runtime import worker_runtime
from core.task_inbox import task_inbox

logger = logging.getLogger(__name__)


class DispatchTools:
    """Manager 调度工具集"""
    
    async def list_workers(self) -> List[Dict[str, Any]]:
        """
        列出所有可用 Worker
        
        Returns:
            List[Worker] - Worker 列表，每个包含 id, name, status, capabilities
        """
        workers = await worker_registry.list_workers()
        return [
            {
                "id": w.get("id"),
                "name": w.get("name"),
                "status": w.get("status"),
                "capabilities": w.get("capabilities", []),
                "backend": w.get("backend"),
            }
            for w in workers
        ]
    
    async def dispatch_worker(
        self,
        worker_id: str,
        instruction: str,
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        派发任务给 Worker 执行
        
        Args:
            worker_id: Worker ID
            instruction: 执行指令（自然语言）
            metadata: 附加元数据
        
        Returns:
            {
                "ok": bool,
                "task_id": str,
                "worker_id": str,
                "message": str
            }
        """
        # 验证 Worker 存在
        worker = await worker_registry.get_worker(worker_id)
        if not worker:
            return {
                "ok": False,
                "error": f"Worker not found: {worker_id}",
                "message": f"Worker {worker_id} 不存在",
            }
        
        # 检查 Worker 状态
        if worker.get("status") == "busy":
            return {
                "ok": False,
                "error": "worker_busy",
                "message": f"Worker {worker_id} 当前忙碌，请选择其他 Worker",
            }
        
        # 执行任务
        try:
            result = await worker_runtime.execute_task(
                worker_id=worker_id,
                source="manager_dispatch",  # 统一来源标识
                instruction=instruction,
                backend=worker.get("backend", "core-agent"),
                metadata=metadata or {},
            )
            
            return {
                "ok": result.get("ok", False),
                "task_id": result.get("task_id", ""),
                "worker_id": worker_id,
                "result": result.get("result", ""),
                "summary": result.get("summary", ""),
                "error": result.get("error"),
                "message": "任务已派发并执行完成" if result.get("ok") else f"执行失败: {result.get('error')}",
            }
        except Exception as e:
            logger.error(f"Dispatch failed: {e}", exc_info=True)
            return {
                "ok": False,
                "error": "dispatch_error",
                "message": f"派发失败: {str(e)}",
            }
    
    async def await_worker_result(self, task_id: str) -> Dict[str, Any]:
        """
        等待 Worker 任务结果（同步等待模式）
        
        Args:
            task_id: 任务 ID
        
        Returns:
            {
                "ok": bool,
                "task_id": str,
                "result": Any,
                "status": str
            }
        """
        # 注意：这是简化实现，实际可能是异步回调
        # WorkerRuntime.execute_task 已经是同步等待模式，结果直接返回
        return {
            "ok": True,
            "task_id": task_id,
            "status": "completed",
            "message": "任务已完成，请查看 result 字段",
        }


dispatch_tools = DispatchTools()
```

**工具定义（供 LLM 使用）**:

```python
# 工具定义格式（供 agent_orchestrator 加载）
DISPATCH_TOOL_DEFINITIONS = [
    {
        "name": "list_workers",
        "description": "列出所有可用的 Worker及其状态、能力描述",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dispatch_worker",
        "description": "派发任务给指定的 Worker 执行。适用于需要执行命令、搜索、长时运行的任务",
        "parameters": {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": "Worker ID（从 list_workers 获取）",
                },
                "instruction": {
                    "type": "string",
                    "description": "执行指令（自然语言描述要做什么）",
                },
                "metadata": {
                    "type": "object",
                    "description": "附加元数据（可选）",
                },
            },
            "required": ["worker_id", "instruction"],
        },
    },
]
```

---

### Step 3: 创建统一扩展执行工具

**目标**：统一所有扩展（Skill）的执行入口，解决候选截断导致误选问题

**新建文件**: `src/core/tools/extension_tools.py`

```python
"""
统一扩展执行工具 - 替代 SkillAgent
"""

import logging
from typing import Any, Dict, Optional

from core.skill_loader import skill_loader

logger = logging.getLogger(__name__)


class ExtensionTools:
    """统一扩展执行工具"""
    
    async def run_extension(
        self,
        skill_name: str,
        args: Dict[str, Any],
        user_id: int = 0,
        context: Any = None,
    ) -> Dict[str, Any]:
        """
        执行指定的扩展（Skill）
        
        Args:
            skill_name: Skill 名称
            args: Skill 参数
            user_id: 用户 ID
            context: 运行时上下文（UnifiedContext）
        
        Returns:
            {
                "ok": bool,
                "skill_name": str,
                "result": str,
                "error": str,
            }
        """
        # 验证 Skill 存在
        skill_info = skill_loader.get_skill(skill_name)
        if not skill_info:
            return {
                "ok": False,
                "error": f"Skill not found: {skill_name}",
                "message": f"未找到技能: {skill_name}",
            }
        
        # 加载 Skill 模块
        try:
            module = skill_loader.import_skill_module(skill_name, "execute.py")
            if not module:
                return {
                    "ok": False,
                    "error": "skill_load_failed",
                    "message": f"无法加载技能: {skill_name}",
                }
            
            # 执行 Skill
            if hasattr(module, "execute"):
                # Skill 执行函数签名: execute(ctx, params, runtime=None)
                result = await module.execute(context, args, None)
                
                # 统一返回格式
                if isinstance(result, str):
                    return {
                        "ok": True,
                        "skill_name": skill_name,
                        "result": result,
                    }
                elif isinstance(result, dict):
                    return {
                        "ok": result.get("ok", True),
                        "skill_name": skill_name,
                        "result": result.get("text", str(result)),
                        "ui": result.get("ui"),
                    }
                else:
                    return {
                        "ok": True,
                        "skill_name": skill_name,
                        "result": str(result),
                    }
            else:
                return {
                    "ok": False,
                    "error": "skill_no_execute",
                    "message": f"技能 {skill_name} 没有 execute 函数",
                }
                
        except Exception as e:
            logger.error(f"Extension execution failed: {skill_name} - {e}", exc_info=True)
            return {
                "ok": False,
                "error": "execution_error",
                "message": f"执行失败: {str(e)}",
            }
    
    async def list_extensions(self) -> list:
        """
        列出所有可用扩展（供 LLM 决策）
        
        Returns:
            List[Dict] - 扩展列表
        """
        skills = skill_loader.get_skills_summary()
        return [
            {
                "name": s.get("name"),
                "description": s.get("description"),
                "triggers": s.get("triggers", []),
            }
            for s in skills
        ]


extension_tools = ExtensionTools()


# 工具定义
EXTENSION_TOOL_DEFINITIONS = [
    {
        "name": "run_extension",
        "description": "执行指定的扩展技能。适用于 RSS 订阅、股票查询、网页抓取等需要特定工具的任务",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "技能名称（如 rss_subscribe, stock_watch, web_browser）",
                },
                "args": {
                    "type": "object",
                    "description": "技能参数",
                },
            },
            "required": ["skill_name", "args"],
        },
    },
    {
        "name": "list_extensions",
        "description": "列出所有可用的扩展技能",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
```

---

### Step 4: 修改 Heartbeat Worker

**目标**：不再自己执行任务，而是生成 Task Item 交给 Manager 决策

**改动文件**: `src/core/heartbeat_worker.py`

**核心改动逻辑**:

```python
# 原逻辑（删除）
# rss_text = await trigger_manual_rss_check(user_id)
# stock_text = await trigger_manual_stock_check(user_id)
# 然后拼接输出

# 新逻辑
# 1. 生成 Task Item
# 2. 交给 Manager（Task Inbox）决策

async def _generate_heartbeat_tasks(self, user_id: str, platform: str) -> List[Dict]:
    """生成 Heartbeat 任务项"""
    from core.task_inbox import task_inbox
    
    tasks = []
    
    # RSS 检查任务
    rss_subs = await get_user_subscriptions(user_id, platform)
    if rss_subs:
        task = await task_inbox.submit(
            source="heartbeat",
            goal="检查 RSS 订阅更新",
            user_id=user_id,
            payload={
                "type": "rss_check",
                "subscriptions": rss_subs,
            },
            priority="normal",
            requires_reply=True,
        )
        tasks.append({
            "task_id": task.task_id,
            "type": "rss_check",
            "description": f"检查 {len(rss_subs)} 个 RSS 订阅更新",
        })
    
    # 股票检查任务
    watchlist = await get_user_watchlist(user_id, platform)
    if watchlist:
        task = await task_inbox.submit(
            source="heartbeat",
            goal="获取自选股行情",
            user_id=user_id,
            payload={
                "type": "stock_check",
                "watchlist": watchlist,
            },
            priority="normal",
            requires_reply=True,
        )
        tasks.append({
            "task_id": task.task_id,
            "type": "stock_check",
            "description": f"获取 {len(watchlist)} 只股票行情",
        })
    
    return tasks


async def run_user_now(self, user_id: str) -> str:
    """执行用户 heartbeat（改为生成任务项）"""
    # ... 现有用户状态检查逻辑 ...
    
    # 生成任务项
    task_items = await self._generate_heartbeat_tasks(user_id, platform)
    
    # 构建输出：告诉用户有哪些待处理任务
    if not task_items:
        return "HEARTBEAT_OK"  # suppress_ok 时返回
    
    # 返回任务列表（Manager 会处理）
    lines = ["🫀 Heartbeat 检测到以下任务：\n"]
    for item in task_items:
        lines.append(f"- {item['description']}")
    
    # 这里不直接执行，而是返回任务列表
    # Manager 会从 Task Inbox 获取并决策
    return "\n".join(lines)
```

**需要删除的代码**:
- `trigger_manual_rss_check()` 调用
- `trigger_manual_stock_check()` 调用
- 直接拼接 RSS/Stock 输出的逻辑

---

### Step 5: 修改 Scheduler (Cron)

**目标**：Cron 任务写入 Task Inbox

**改动文件**: `src/core/scheduler.py`

```python
# 原逻辑
async def run_skill_cron_job(job):
    # 直接执行 Skill
    
# 新逻辑
async def run_skill_cron_job(job):
    from core.task_inbox import task_inbox
    
    # 从 job 中获取任务信息
    instruction = job.get("instruction", "")
    user_id = job.get("user_id", 0)
    platform = job.get("platform", "telegram")
    
    # 写入 Task Inbox
    task = await task_inbox.submit(
        source="cron",
        goal=instruction,
        user_id=user_id,
        payload={
            "type": "scheduled_task",
            "crontab": job.get("crontab"),
            "instruction": instruction,
        },
        priority="low",  # Cron 任务默认低优先级
        requires_reply=True,
    )
    
    logger.info(f"Cron task submitted: {task.task_id}")
    # 后续由 Manager 从 Task Inbox 获取并决策执行
```

---

### Step 6: 修改用户对话处理

**目标**：用户对话写入 Task Inbox，由 Manager 统一决策

**改动文件**: `src/handlers/ai_handlers.py`

**核心改动**:

```python
# 移除硬编码的调度逻辑：
# - 删除 _is_worker_status_query()
# - 删除 _looks_like_shell_command()
# - 删除 _resolve_worker_delegate_mode()
# - 删除 intent_router 依赖

# 改为：写入 Task Inbox，让 Manager 决策

async def handle_ai_chat(ctx: UnifiedContext) -> None:
    user_message = ctx.message.text
    
    # 写入 Task Inbox
    from core.task_inbox import task_inbox
    
    task = await task_inbox.submit(
        source="user_chat",
        goal=user_message,
        user_id=ctx.message.user.id,
        payload={
            "platform": ctx.message.platform,
            "message_id": ctx.message.id,
        },
        priority="high",
        requires_reply=True,
    )
    
    # 触发 Manager 处理（通过 Agent Orchestrator）
    from core.agent_orchestrator import agent_orchestrator
    
    # 构建任务消息
    task_message = f"[Task {task.task_id}] {user_message}"
    
    # 让 Orchestrator 处理
    message_history = [{"role": "user", "parts": [{"text": task_message}]}]
    
    async for chunk in agent_orchestrator.handle_message(ctx, message_history):
        if chunk:
            await ctx.reply(chunk)
    
    # 标记任务完成
    # （实际应该在 orchestrator 内部完成）
```

---

### Step 7: 修改 Agent Orchestrator

**目标**：集成 Task Inbox，添加工具执行器

**改动文件**: `src/core/agent_orchestrator.py`

**需要添加的内容**:

```python
# 1. 导入新工具
from core.tools.dispatch_tools import dispatch_tools, DISPATCH_TOOL_DEFINITIONS
from core.tools.extension_tools import extension_tools, EXTENSION_TOOL_DEFINITIONS


class AgentOrchestrator:
    def __init__(self):
        # ... 现有初始化 ...
        
        # 添加工具定义
        self._dispatch_tool_defs = DISPATCH_TOOL_DEFINITIONS
        self._extension_tool_defs = EXTENSION_TOOL_DEFINITIONS
    
    async def _get_tool_definitions(self, user_id: int) -> List[Dict]:
        """获取工具定义（包括新增的调度和扩展工具）"""
        # 现有逻辑 ...
        
        # 添加 Manager 调度工具
        tools.extend(self._dispatch_tool_defs)
        
        # 添加扩展工具
        tools.extend(self._extension_tool_defs)
        
        return tools
    
    async def _execute_tool(self, tool_name: str, args: Dict) -> Dict:
        """工具执行器"""
        
        # 调度工具
        if tool_name == "list_workers":
            return await dispatch_tools.list_workers()
        
        if tool_name == "dispatch_worker":
            return await dispatch_tools.dispatch_worker(
                worker_id=args.get("worker_id"),
                instruction=args.get("instruction"),
                metadata=args.get("metadata", {}),
            )
        
        # 扩展工具
        if tool_name == "run_extension":
            return await extension_tools.run_extension(
                skill_name=args.get("skill_name"),
                args=args.get("args", {}),
            )
        
        if tool_name == "list_extensions":
            return await extension_tools.list_extensions()
        
        # 现有工具执行 ...
```

---

### Step 8: 简化 Worker Runtime

**目标**：Worker 只执行任务，不知道任务来源，不做特判

**改动文件**: `src/core/worker_runtime.py`

**需要删除/简化的代码**:

```python
# 1. 删除 shell hint 自动切换逻辑
# 原代码：
if (
    selected_backend in {"core-agent", "codex", "gemini-cli"}
    and normalized_source in {"user", "user_cmd", "user_chat"}
    and self._looks_like_shell_command(instruction)
):
    selected_backend = "shell"

# 2. 删除其他来源特判逻辑
# 只保留：backend 验证、workspace 解析、命令执行

# 3. 简化任务结果返回
async def execute_task(self, worker_id, source, instruction, backend=None, metadata=None):
    # 统一的执行入口
    # 不再根据 source 判断行为
    # 只根据 backend 和 worker 配置执行
    
    # 结果格式统一为：
    return {
        "ok": bool,
        "task_id": str,
        "result": str,       # 原始执行结果
        "summary": str,      # 结果摘要
        "error": str,        # 错误信息
    }
```

---

### Step 9: 移除 Skill Agent

**目标**：彻底从主路径移除 SkillAgent

**改动文件**:
- `src/agents/skill_agent.py` - 删除或标记废弃
- 所有调用 `skill_agent.execute_skill()` 的地方改为使用 `extension_tools.run_extension()`

**需要修改的文件**:
- `skills/builtin/notebooklm/scripts/execute.py` - 第 192 行

```python
# 原代码：
from agents.skill_agent import skill_agent
async for chunk, files, result_obj in skill_agent.execute_skill(...):

# 改为：
from core.tools.extension_tools import extension_tools
result = await extension_tools.run_extension(
    skill_name="web_browser",
    args={"url": source_url},
)
```

---

### Step 10: 更新 SOUL 和 Prompts

**目标**：让 Manager 和 Worker 知道新的架构

**改动文件**: `src/core/prompts.py`

```python
# 新增 Manager SOUL
MANAGER_CORE_PROMPT = """你是 X-Bot 的 Core Manager，负责协调整个系统。

## 架构说明
- 你（Manager）负责决策：理解用户意图，决定是否派发给 Worker
- 所有任务都通过 Task Inbox 统一管理
- 你有调度工具可以派发任务给 Worker

## 你的工具
1. 四原语：read, write, edit, bash - 基础文件/命令操作
2. list_workers - 查看可用 Worker
3. dispatch_worker - 派发任务给 Worker
4. run_extension - 执行扩展技能

## 决策原则
- 简单任务（闲聊、问候）→ 自己处理
- 需要执行命令/搜索/长时任务 → 派发给 Worker
- 需要特定工具（RSS、股票）→ 使用 run_extension

## 输出规范
- 用户只看到你的最终回复
- 不要暴露 Worker 内部细节
"""


# 新增 Worker SOUL
WORKER_PROMPT = """你是 X-Bot 的执行 Worker (Atlas)。

## 你的职责
- 执行 Manager 派发的任务
- 只执行指令，不问为什么
- 执行完成后返回结构化结果

## 你的工具
- 四原语：read, write, edit, bash
- run_extension：执行扩展技能

## 执行原则
- 先执行，后汇报
- 输出结构化、可复用、可验证的结果
- 不要暴露内部实现细节
"""
```

---

## 五、测试用例设计

### 5.1 任务入口测试

```python
# tests/core/test_task_inbox.py

import pytest
from core.task_inbox import task_inbox, TaskEnvelope


@pytest.mark.asyncio
async def test_submit_task():
    task = await task_inbox.submit(
        source="user_chat",
        goal="帮我查一下今天的新闻",
        user_id=12345,
    )
    assert task.task_id
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_dispatch_flow():
    # 1. 提交任务
    task = await task_inbox.submit(
        source="heartbeat",
        goal="检查 RSS 更新",
        user_id=12345,
    )
    
    # 2. Manager 派发
    await task_inbox.assign_worker(task.task_id, "worker-main", "需要执行命令")
    
    # 3. 完成任务
    await task_inbox.complete(
        task.task_id,
        result={"updates": 5},
        final_output="发现 5 条更新",
    )
    
    updated = await task_inbox.get(task.task_id)
    assert updated.status == "completed"
    assert updated.final_output == "发现 5 条更新"
```

### 5.2 多来源任务测试

```python
# tests/core/test_multi_source_tasks.py

@pytest.mark.asyncio
async def test_user_chat_task():
    task = await task_inbox.submit(
        source="user_chat",
        goal="写一首诗",
        user_id=123,
        requires_reply=True,
    )
    assert task.source == "user_chat"
    assert task.requires_reply


@pytest.mark.asyncio
async def test_heartbeat_task():
    task = await task_inbox.submit(
        source="heartbeat",
        goal="检查 RSS 更新",
        user_id=123,
        payload={"type": "rss_check"},
    )
    assert task.source == "heartbeat"


@pytest.mark.asyncio
async def test_cron_task():
    task = await task_inbox.submit(
        source="cron",
        goal="每天早上 8 点推送天气",
        user_id=123,
        payload={"crontab": "0 8 * * *"},
    )
    assert task.source == "cron"
```

### 5.3 Worker 调度测试

```python
# tests/core/test_dispatch.py

@pytest.mark.asyncio
async def test_dispatch_to_worker():
    from core.tools.dispatch_tools import dispatch_tools
    
    result = await dispatch_tools.dispatch_worker(
        worker_id="worker-main",
        instruction="列出当前目录文件",
    )
    
    assert result["ok"]
    assert result["worker_id"] == "worker-main"


@pytest.mark.asyncio
async def test_dispatch_to_busy_worker():
    # 先占用 worker
    # 然后尝试派发
    result = await dispatch_tools.dispatch_worker(
        worker_id="worker-busy",
        instruction="执行任务",
    )
    
    assert not result["ok"]
    assert result["error"] == "worker_busy"
```

---

## 六、验收标准

### 6.1 功能验收

| 验收项 | 标准 |
|--------|------|
| 任务统一入口 | 所有来源（user_chat/heartbeat/cron）都写入 Task Inbox |
| Manager 决策 | Manager 能自主决定是否派发 Worker、使用哪个 Worker |
| 工具可用 | dispatch_worker、list_workers、run_extension 工具可正常调用 |
| Worker 简化 | Worker 不再根据 source 做特判，只执行指令 |
| 结果统一 | 用户只看到 Manager 输出，Worker 原始输出不直出 |
| Skill Agent 移除 | 主执行路径不再使用 SkillAgent |

### 6.2 回归测试

| 测试场景 | 预期行为 |
|---------|---------|
| 用户发送"你好" | Manager 直接回复，不派发 Worker |
| 用户发送"帮我查新闻" | Manager 派发给 Worker 执行 |
| Heartbeat 触发 | 生成 Task Item，Manager 决策 |
| Cron 定时任务 | 写入 Task Inbox，Manager 决策 |
| RSS/Stock 技能调用 | 通过 run_extension 执行 |
| Worker 执行完成 | 结果返回给 Manager，Manager 整合输出 |

---

## 七、文件变更汇总

### 7.1 新建 (3 个文件)

```
src/core/task_inbox.py          # 统一任务入口
src/core/tools/                 # 新目录
src/core/tools/__init__.py
src/core/tools/dispatch_tools.py # Manager 调度工具
src/core/tools/extension_tools.py # 统一扩展执行工具
```

### 7.2 修改 (9 个文件)

```
src/core/heartbeat_worker.py    # 改为生成 Task Item
src/core/scheduler.py           # Cron 写入 Task Inbox
src/handlers/ai_handlers.py     # 对话写入 Task Inbox
src/core/agent_orchestrator.py   # 集成工具执行器
src/core/worker_runtime.py       # 简化执行逻辑
src/core/tool_access_store.py    # 移除来源特判
src/core/prompts.py             # 更新 SOUL
src/core/soul_store.py          # SOUL 动态加载
skills/builtin/notebooklm/scripts/execute.py  # 移除 SkillAgent 调用
```

### 7.3 删除 (1 个文件)

```
src/agents/skill_agent.py       # 彻底移除（或标记废弃）
```

---

## 八、实施顺序

1. **Step 1**: 创建 Task Inbox
2. **Step 2**: 创建 Manager 调度工具
3. **Step 3**: 创建统一扩展执行工具
4. **Step 4**: 修改 Heartbeat Worker
5. **Step 5**: 修改 Scheduler
6. **Step 6**: 修改用户对话处理
7. **Step 7**: 修改 Agent Orchestrator
8. **Step 8**: 简化 Worker Runtime
9. **Step 9**: 移除 Skill Agent
10. **Step 10**: 更新 Prompts/SOUL
11. **测试**: 运行测试用例验证
12. **部署**: 重新构建并发布

---

## 九、风险控制

| 风险 | 缓解措施 |
|------|---------|
| 调度决策失误导致功能退化 | 保留环境变量降级机制（如 DISPATCH_MODEL_ROUTING） |
| 任务丢失 | Task Inbox 持久化到磁盘，定期清理已完成任务 |
| Worker 选择不当 | 添加 Worker 健康状态检查，失败自动重试其他 Worker |
| 扩展执行失败 | 统一错误处理，返回友好错误信息给 Manager |

---

*文档版本: v1.0*
*生成日期: 2026-02-16*
