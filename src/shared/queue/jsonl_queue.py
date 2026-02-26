from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List


class FileLock:
    def __init__(
        self,
        lock_path: Path,
        *,
        timeout_sec: float = 8.0,
        poll_sec: float = 0.05,
    ) -> None:
        self.lock_path = lock_path
        self.timeout_sec = max(0.2, float(timeout_sec))
        self.poll_sec = max(0.01, float(poll_sec))
        self._held = False

    async def __aenter__(self) -> "FileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                os.write(fd, f"pid={os.getpid()}\n".encode("utf-8"))
                os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"queue lock timeout: {self.lock_path}")
                await asyncio.sleep(self.poll_sec)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._held:
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass
        self._held = False
        return False


class JsonlTable:
    def __init__(self, path: str) -> None:
        self.path = Path(str(path or "")).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._inproc_lock = asyncio.Lock()

    def _read_all_unlocked(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    raw = str(line or "").strip()
                    if not raw:
                        continue
                    try:
                        item = json.loads(raw)
                    except Exception:
                        continue
                    if isinstance(item, dict):
                        rows.append(dict(item))
        except Exception:
            return []
        return rows

    def _write_all_unlocked(self, rows: List[Dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(dict(row or {}), ensure_ascii=False))
                f.write("\n")
        tmp.replace(self.path)

    async def read_all(self) -> List[Dict[str, Any]]:
        async with self._inproc_lock:
            async with FileLock(self.lock_path):
                return self._read_all_unlocked()

    async def write_all(self, rows: List[Dict[str, Any]]) -> None:
        async with self._inproc_lock:
            async with FileLock(self.lock_path):
                self._write_all_unlocked(rows)

    async def append(self, row: Dict[str, Any]) -> None:
        async with self._inproc_lock:
            async with FileLock(self.lock_path):
                rows = self._read_all_unlocked()
                rows.append(dict(row or {}))
                self._write_all_unlocked(rows)
