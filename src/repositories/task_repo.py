"""
Scheduled Tasks Repository
"""

import logging
import aiosqlite
from typing import List, Dict
from repositories.base import get_db

logger = logging.getLogger(__name__)


async def add_scheduled_task(skill_name: str, crontab: str, instruction: str) -> int:
    """Add a new scheduled task"""
    async with await get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO scheduled_tasks (skill_name, crontab, instruction, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (skill_name, crontab, instruction),
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_active_tasks() -> List[Dict]:
    """Get all active scheduled tasks"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_tasks WHERE is_active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_task_status(task_id: int, is_active: bool):
    """Enable/Disable a task"""
    async with await get_db() as db:
        await db.execute(
            "UPDATE scheduled_tasks SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if is_active else 0, task_id),
        )
        await db.commit()


async def delete_task(task_id: int):
    """Delete a task"""
    async with await get_db() as db:
        await db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        await db.commit()


async def get_tasks_by_skill(skill_name: str) -> List[Dict]:
    """Get tasks for a specific skill"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_tasks WHERE skill_name = ?", (skill_name,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
