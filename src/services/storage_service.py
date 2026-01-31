import sqlite3
import logging
from typing import Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class StorageService:
    """
    Simple Key-Value Storage for Skills.
    Backed by SQLite.
    """
    
    def __init__(self, db_path: str = "data/skill_data.db"):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        """Initialize the storage table"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS skill_kv_store (
                        skill_name TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (skill_name, key)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to init skill storage: {e}")

    def put(self, skill_name: str, key: str, value: str) -> bool:
        """Save a value"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO skill_kv_store (skill_name, key, value)
                    VALUES (?, ?, ?)
                    ON CONFLICT(skill_name, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """, (skill_name, key, str(value)))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Storage put failed ({skill_name}:{key}): {e}")
            return False

    def get(self, skill_name: str, key: str, default: Any = None) -> Optional[str]:
        """Retrieve a value"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT value FROM skill_kv_store WHERE skill_name = ? AND key = ?",
                    (skill_name, key)
                )
                row = cursor.fetchone()
                return row[0] if row else default
        except Exception as e:
            logger.error(f"Storage get failed ({skill_name}:{key}): {e}")
            return default

    def delete(self, skill_name: str, key: str) -> bool:
        """Delete a value"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM skill_kv_store WHERE skill_name = ? AND key = ?",
                    (skill_name, key)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Storage delete failed ({skill_name}:{key}): {e}")
            return False

    def list_keys(self, skill_name: str) -> list:
        """List all keys for a skill"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT key FROM skill_kv_store WHERE skill_name = ?",
                    (skill_name,)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Storage list failed ({skill_name}): {e}")
            return []

# Global Instance
storage_service = StorageService()
