from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_task_id() -> str:
    return f"dev-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}"


def _default_root() -> Path:
    data_dir = str(os.getenv("DATA_DIR", "/app/data") or "/app/data").strip()
    root = str(os.getenv("DEV_TASKS_ROOT", "") or "").strip()
    if not root:
        root = os.path.join(data_dir, "system", "dev_tasks")
    return Path(root).resolve()


class DevTaskStore:
    def __init__(self) -> None:
        self.root = _default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, task_id: str) -> Path:
        safe = str(task_id or "").strip()
        return (self.root / f"{safe}.json").resolve()

    async def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(payload or {})
        if not str(record.get("task_id") or "").strip():
            record["task_id"] = _new_task_id()
        now = _now_iso()
        record["created_at"] = str(record.get("created_at") or now)
        record["updated_at"] = now
        if not isinstance(record.get("events"), list):
            record["events"] = []
        await self.save(record)
        return record

    async def load(self, task_id: str) -> Dict[str, Any] | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        async with self._lock:
            try:
                raw = path.read_text(encoding="utf-8")
                loaded = json.loads(raw)
            except Exception:
                return None
        if not isinstance(loaded, dict):
            return None
        return loaded

    async def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(payload or {})
        task_id = str(record.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        record["updated_at"] = _now_iso()
        path = self._path(task_id)
        async with self._lock:
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return record

    async def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit or 20)))
        rows: List[Dict[str, Any]] = []
        async with self._lock:
            paths = sorted(
                self.root.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for path in paths[: safe_limit * 2]:
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(loaded, dict):
                    rows.append(loaded)
        rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return rows[:safe_limit]


dev_task_store = DevTaskStore()
