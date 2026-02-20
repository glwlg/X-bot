import logging
import json
from typing import Optional, List, Dict, Any
from .base import get_db

logger = logging.getLogger(__name__)


async def add_account(user_id: int, service: str, data: Dict[str, Any]) -> bool:
    """添加或更新账号"""
    try:
        data_json = json.dumps(data, ensure_ascii=False)
        async with await get_db() as db:
            await db.execute(
                """
                INSERT INTO accounts (user_id, service, enc_data)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, service) DO UPDATE SET
                    enc_data = excluded.enc_data,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, service, data_json),
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding account: {e}")
        return False


async def get_account(user_id: int, service: str) -> Optional[Dict[str, Any]]:
    """获取账号详情"""
    try:
        async with await get_db() as db:
            async with db.execute(
                "SELECT enc_data FROM accounts WHERE user_id = ? AND service = ?",
                (user_id, service),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return json.loads(row[0])
        return None
    except Exception as e:
        logger.error(f"Error getting account: {e}")
        return None


async def list_accounts(user_id: int) -> List[str]:
    """列出所有已保存的账号服务名"""
    try:
        async with await get_db() as db:
            async with db.execute(
                "SELECT service FROM accounts WHERE user_id = ? ORDER BY service",
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        return []


async def delete_account(user_id: int, service: str) -> bool:
    """删除账号"""
    try:
        async with await get_db() as db:
            await db.execute(
                "DELETE FROM accounts WHERE user_id = ? AND service = ?",
                (user_id, service),
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Error deleting account: {e}")
        return False
