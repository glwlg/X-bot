"""
Task Manager - 管理用户正在执行的长时间任务
支持 /stop 命令中断任意任务
"""

import asyncio
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ActiveTask:
    """表示一个正在执行的任务"""

    task: asyncio.Task
    description: str
    started_at: datetime = field(default_factory=datetime.now)
    cancel_requested: bool = False


class TaskManager:
    """
    管理用户的活动任务。
    允许注册、跟踪和取消任务。
    """

    def __init__(self):
        # user_id -> ActiveTask
        self._tasks: Dict[str, ActiveTask] = {}
        self._lock = asyncio.Lock()

    async def register_task(
        self, user_id: str, task: asyncio.Task, description: str = "AI 对话"
    ) -> None:
        """
        注册一个用户的活动任务。
        如果该用户已有任务，先取消旧任务。
        """
        user_id = str(user_id)
        async with self._lock:
            # 如果已存在任务，取消它
            if user_id in self._tasks:
                old_task = self._tasks[user_id]
                if not old_task.task.done():
                    old_task.task.cancel()
                    logger.info(f"[TaskManager] Cancelled old task for user {user_id}")

            self._tasks[user_id] = ActiveTask(
                task=task,
                description=description,
            )
            logger.debug(
                f"[TaskManager] Registered task for user {user_id}: {description}"
            )

    async def cancel_task(self, user_id: str) -> Optional[str]:
        """
        取消用户的当前任务。
        返回被取消任务的描述，如果没有活动任务则返回 None。
        """
        user_id = str(user_id)
        async with self._lock:
            if user_id not in self._tasks:
                return None

            active_task = self._tasks[user_id]

            if active_task.task.done():
                # 任务已完成，清理
                del self._tasks[user_id]
                return None

            # 标记取消请求
            active_task.cancel_requested = True

            # 取消任务
            active_task.task.cancel()
            description = active_task.description

            # 从注册表中移除
            del self._tasks[user_id]

            logger.info(
                f"[TaskManager] Cancelled task for user {user_id}: {description}"
            )
            return description

    def is_cancelled(self, user_id: str) -> bool:
        """
        检查用户的任务是否已请求取消。
        可用于在任务内部主动检查并优雅退出。
        """
        user_id = str(user_id)
        if user_id not in self._tasks:
            return False
        return self._tasks[user_id].cancel_requested

    def has_active_task(self, user_id: str) -> bool:
        """检查用户是否有活动任务"""
        user_id = str(user_id)
        if user_id not in self._tasks:
            return False
        return not self._tasks[user_id].task.done()

    def get_task_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户当前任务的信息"""
        user_id = str(user_id)
        if user_id not in self._tasks:
            return None

        task = self._tasks[user_id]
        if task.task.done():
            return None

        return {
            "description": task.description,
            "started_at": task.started_at,
            "running_seconds": (datetime.now() - task.started_at).total_seconds(),
        }

    async def cleanup_completed(self) -> None:
        """清理已完成的任务"""
        async with self._lock:
            completed = [uid for uid, task in self._tasks.items() if task.task.done()]
            for uid in completed:
                del self._tasks[uid]

    def unregister_task(self, user_id: str) -> None:
        """
        从注册表中移除任务（任务正常完成时调用）
        """
        user_id = str(user_id)
        if user_id in self._tasks:
            del self._tasks[user_id]
            logger.debug(f"[TaskManager] Unregistered task for user {user_id}")


# 全局单例
task_manager = TaskManager()
