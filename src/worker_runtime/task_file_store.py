from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from core.worker_store import worker_registry

TASK_BEGIN_MARKER = "<!-- XBOT_TASKS_BEGIN -->"
TASK_END_MARKER = "<!-- XBOT_TASKS_END -->"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _WorkerFileLock:
    def __init__(
        self,
        lock_path: Path,
        *,
        timeout_sec: float = 8.0,
        poll_sec: float = 0.05,
    ):
        self.lock_path = lock_path
        self.timeout_sec = max(0.2, float(timeout_sec))
        self.poll_sec = max(0.01, float(poll_sec))
        self._held = False

    async def __aenter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                os.write(fd, f"pid={os.getpid()} at={_now_iso()}\n".encode("utf-8"))
                os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"worker task lock timeout: {self.lock_path}")
                await asyncio.sleep(self.poll_sec)

    async def __aexit__(self, exc_type, exc, tb):
        if self._held:
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass
        self._held = False
        return False


class WorkerTaskFileStore:
    """Filesystem queue backed by each worker's TASK.md/TASK_HISTORY.md."""

    def __init__(self) -> None:
        self._inproc_lock = asyncio.Lock()

    @staticmethod
    def _task_md_path(worker: Dict[str, Any]) -> Path:
        root = Path(str(worker.get("workspace_root") or "")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return (root / "TASK.md").resolve()

    @staticmethod
    def _history_md_path(worker: Dict[str, Any]) -> Path:
        root = Path(str(worker.get("workspace_root") or "")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return (root / "TASK_HISTORY.md").resolve()

    @staticmethod
    def _lock_path(worker: Dict[str, Any]) -> Path:
        root = Path(str(worker.get("workspace_root") or "")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return (root / ".task.lock").resolve()

    @staticmethod
    def _default_payload(worker_id: str) -> Dict[str, Any]:
        return {
            "version": 1,
            "worker_id": str(worker_id or "worker-main"),
            "updated_at": _now_iso(),
            "tasks": [],
        }

    @staticmethod
    def _render_task_md(payload: Dict[str, Any]) -> str:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return (
            "# TASK.md\n\n"
            "Managed by X-Bot worker runtime.\n"
            "Do not edit while worker is running.\n\n"
            f"{TASK_BEGIN_MARKER}\n"
            "```json\n"
            f"{body}\n"
            "```\n"
            f"{TASK_END_MARKER}\n"
        )

    @staticmethod
    def _extract_json_block(text: str) -> str:
        raw = str(text or "")
        start = raw.find(TASK_BEGIN_MARKER)
        end = raw.find(TASK_END_MARKER)
        if start < 0 or end < 0 or end <= start:
            return ""
        section = raw[start + len(TASK_BEGIN_MARKER) : end].strip()
        if section.startswith("```json"):
            section = section[len("```json") :].lstrip("\r\n")
        if section.endswith("```"):
            section = section[:-3].rstrip()
        return section.strip()

    def _read_payload_unlocked(self, path: Path, worker_id: str) -> Dict[str, Any]:
        default = self._default_payload(worker_id)
        if not path.exists():
            return default
        try:
            text = path.read_text(encoding="utf-8")
            block = self._extract_json_block(text)
            if not block:
                return default
            loaded = json.loads(block)
            if not isinstance(loaded, dict):
                return default
            payload = dict(default)
            payload.update(loaded)
            tasks = payload.get("tasks")
            payload["tasks"] = tasks if isinstance(tasks, list) else []
            return payload
        except Exception:
            return default

    def _write_payload_unlocked(self, path: Path, payload: Dict[str, Any]) -> None:
        data = dict(payload or {})
        data["updated_at"] = _now_iso()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self._render_task_md(data), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _normalize_task(row: Dict[str, Any], *, worker_id: str) -> Dict[str, Any]:
        data = dict(row or {})
        data["job_id"] = str(data.get("job_id") or "").strip()
        data["worker_id"] = str(data.get("worker_id") or worker_id).strip() or worker_id
        data["session_id"] = str(data.get("session_id") or "").strip()
        data["instruction"] = str(data.get("instruction") or "").strip()
        data["source"] = str(data.get("source") or "manager_dispatch").strip()
        data["backend"] = str(data.get("backend") or "").strip()
        data["status"] = str(data.get("status") or "pending").strip().lower()
        data["created_at"] = str(data.get("created_at") or _now_iso())
        data["updated_at"] = str(data.get("updated_at") or data["created_at"])
        data["started_at"] = str(data.get("started_at") or "")
        data["ended_at"] = str(data.get("ended_at") or "")
        data["claimed_by"] = str(data.get("claimed_by") or "")
        data["error"] = str(data.get("error") or "")
        data["delivered_at"] = str(data.get("delivered_at") or "")
        metadata = data.get("metadata")
        data["metadata"] = dict(metadata) if isinstance(metadata, dict) else {}
        result = data.get("result")
        data["result"] = dict(result) if isinstance(result, dict) else {}
        return data

    async def _resolve_worker(self, worker_id: str) -> Dict[str, Any]:
        safe_id = str(worker_id or "").strip() or "worker-main"
        worker = await worker_registry.get_worker(safe_id)
        if worker:
            return dict(worker)
        worker = await worker_registry.ensure_default_worker(safe_id)
        return dict(worker)

    async def _candidate_workers(self, worker_id: str = "") -> List[Dict[str, Any]]:
        safe_id = str(worker_id or "").strip()
        if safe_id:
            return [await self._resolve_worker(safe_id)]

        workers = await worker_registry.list_workers()
        if not workers:
            workers = [await worker_registry.ensure_default_worker()]
        rows = [dict(item) for item in workers if isinstance(item, dict)]
        rows.sort(key=lambda item: str(item.get("id") or ""))
        return rows

    async def ensure_task_files(self, *, worker_id: str = "") -> None:
        workers = await self._candidate_workers(worker_id)
        for worker in workers:
            safe_worker_id = (
                str(worker.get("id") or "worker-main").strip() or "worker-main"
            )
            path = self._task_md_path(worker)
            lock_path = self._lock_path(worker)
            async with self._inproc_lock:
                try:
                    async with _WorkerFileLock(lock_path):
                        payload = self._read_payload_unlocked(path, safe_worker_id)
                        tasks = payload.get("tasks")
                        payload["tasks"] = tasks if isinstance(tasks, list) else []
                        self._write_payload_unlocked(path, payload)
                except TimeoutError:
                    continue

    @staticmethod
    def _append_task_history_unlocked(path: Path, task: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            base = path.read_text(encoding="utf-8")
        else:
            base = "# TASK_HISTORY.md\n\nArchived worker tasks.\n\n"

        instruction = str(task.get("instruction") or "").strip()
        metadata_json = json.dumps(
            task.get("metadata") or {}, ensure_ascii=False, indent=2
        )
        result_json = json.dumps(task.get("result") or {}, ensure_ascii=False, indent=2)
        entry = (
            f"## {str(task.get('job_id') or '').strip()}\n"
            f"- status: {str(task.get('status') or '').strip()}\n"
            f"- worker_id: {str(task.get('worker_id') or '').strip()}\n"
            f"- session_id: {str(task.get('session_id') or '').strip()}\n"
            f"- source: {str(task.get('source') or '').strip()}\n"
            f"- backend: {str(task.get('backend') or '').strip()}\n"
            f"- created_at: {str(task.get('created_at') or '').strip()}\n"
            f"- started_at: {str(task.get('started_at') or '').strip()}\n"
            f"- ended_at: {str(task.get('ended_at') or '').strip()}\n"
            f"- delivered_at: {str(task.get('delivered_at') or '').strip()}\n\n"
            "### Instruction\n"
            "```text\n"
            f"{instruction}\n"
            "```\n\n"
            "### Metadata\n"
            "```json\n"
            f"{metadata_json}\n"
            "```\n\n"
            "### Result\n"
            "```json\n"
            f"{result_json}\n"
            "```\n\n"
        )
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(base.rstrip() + "\n\n" + entry, encoding="utf-8")
        tmp.replace(path)

    async def submit(
        self,
        *,
        worker_id: str,
        instruction: str,
        source: str,
        backend: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        worker = await self._resolve_worker(worker_id)
        safe_worker_id = str(worker.get("id") or "worker-main").strip() or "worker-main"
        now = _now_iso()
        meta = dict(metadata or {})
        session_id = (
            str(meta.get("session_id") or "").strip()
            or f"session-{int(datetime.now().timestamp())}"
        )
        task = {
            "job_id": f"wj-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}",
            "worker_id": safe_worker_id,
            "session_id": session_id,
            "instruction": str(instruction or "").strip(),
            "source": str(source or "manager_dispatch").strip() or "manager_dispatch",
            "backend": str(backend or "").strip(),
            "metadata": meta,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "ended_at": "",
            "claimed_by": "",
            "result": {},
            "error": "",
            "delivered_at": "",
        }

        path = self._task_md_path(worker)
        lock_path = self._lock_path(worker)
        async with self._inproc_lock:
            async with _WorkerFileLock(lock_path):
                payload = self._read_payload_unlocked(path, safe_worker_id)
                tasks = [
                    self._normalize_task(item, worker_id=safe_worker_id)
                    for item in list(payload.get("tasks") or [])
                    if isinstance(item, dict)
                ]
                tasks.append(self._normalize_task(task, worker_id=safe_worker_id))
                payload["tasks"] = tasks
                self._write_payload_unlocked(path, payload)
        return dict(task)

    async def claim_next(
        self,
        *,
        claimer: str,
        worker_id: str = "",
    ) -> Dict[str, Any] | None:
        claimer_id = str(claimer or "worker-daemon").strip() or "worker-daemon"
        workers = await self._candidate_workers(worker_id)
        for worker in workers:
            safe_worker_id = (
                str(worker.get("id") or "worker-main").strip() or "worker-main"
            )
            path = self._task_md_path(worker)
            lock_path = self._lock_path(worker)
            async with self._inproc_lock:
                try:
                    async with _WorkerFileLock(lock_path):
                        payload = self._read_payload_unlocked(path, safe_worker_id)
                        tasks = [
                            self._normalize_task(item, worker_id=safe_worker_id)
                            for item in list(payload.get("tasks") or [])
                            if isinstance(item, dict)
                        ]
                        picked: Dict[str, Any] | None = None
                        for row in tasks:
                            if (
                                str(row.get("status") or "").strip().lower()
                                != "pending"
                            ):
                                continue
                            now = _now_iso()
                            row["status"] = "running"
                            row["claimed_by"] = claimer_id
                            row["started_at"] = now
                            row["updated_at"] = now
                            picked = dict(row)
                            break
                        if picked is None:
                            continue
                        payload["tasks"] = tasks
                        self._write_payload_unlocked(path, payload)
                        return picked
                except TimeoutError:
                    continue
        return None

    async def finish(
        self,
        job_id: str,
        *,
        ok: bool,
        result: Dict[str, Any] | None = None,
        error: str = "",
    ) -> Dict[str, Any] | None:
        key = str(job_id or "").strip()
        if not key:
            return None
        workers = await self._candidate_workers()
        for worker in workers:
            safe_worker_id = (
                str(worker.get("id") or "worker-main").strip() or "worker-main"
            )
            path = self._task_md_path(worker)
            lock_path = self._lock_path(worker)
            async with self._inproc_lock:
                try:
                    async with _WorkerFileLock(lock_path):
                        payload = self._read_payload_unlocked(path, safe_worker_id)
                        tasks = [
                            self._normalize_task(item, worker_id=safe_worker_id)
                            for item in list(payload.get("tasks") or [])
                            if isinstance(item, dict)
                        ]
                        changed: Dict[str, Any] | None = None
                        for row in tasks:
                            if str(row.get("job_id") or "").strip() != key:
                                continue
                            now = _now_iso()
                            row["status"] = "done" if ok else "failed"
                            row["ended_at"] = now
                            row["updated_at"] = now
                            row["result"] = dict(result or {})
                            row["error"] = str(error or "")
                            changed = dict(row)
                            break
                        if changed is None:
                            continue
                        payload["tasks"] = tasks
                        self._write_payload_unlocked(path, payload)
                        return changed
                except TimeoutError:
                    continue
        return None

    async def list_undelivered(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(200, int(limit or 20)))
        workers = await self._candidate_workers()
        rows: List[Dict[str, Any]] = []
        for worker in workers:
            safe_worker_id = (
                str(worker.get("id") or "worker-main").strip() or "worker-main"
            )
            path = self._task_md_path(worker)
            payload = self._read_payload_unlocked(path, safe_worker_id)
            tasks = [
                self._normalize_task(item, worker_id=safe_worker_id)
                for item in list(payload.get("tasks") or [])
                if isinstance(item, dict)
            ]
            for row in tasks:
                status = str(row.get("status") or "").strip().lower()
                if status not in {"done", "failed"}:
                    continue
                if str(row.get("delivered_at") or "").strip():
                    continue
                rows.append(row)
        rows.sort(
            key=lambda item: (
                str(item.get("ended_at") or ""),
                str(item.get("updated_at") or ""),
                str(item.get("created_at") or ""),
            )
        )
        return rows[:safe_limit]

    async def mark_delivered(
        self,
        job_id: str,
        *,
        detail: str = "",
    ) -> bool:
        key = str(job_id or "").strip()
        if not key:
            return False
        workers = await self._candidate_workers()
        for worker in workers:
            safe_worker_id = (
                str(worker.get("id") or "worker-main").strip() or "worker-main"
            )
            task_path = self._task_md_path(worker)
            history_path = self._history_md_path(worker)
            lock_path = self._lock_path(worker)
            async with self._inproc_lock:
                try:
                    async with _WorkerFileLock(lock_path):
                        payload = self._read_payload_unlocked(task_path, safe_worker_id)
                        tasks = [
                            self._normalize_task(item, worker_id=safe_worker_id)
                            for item in list(payload.get("tasks") or [])
                            if isinstance(item, dict)
                        ]
                        archived: Dict[str, Any] | None = None
                        remaining: List[Dict[str, Any]] = []
                        for row in tasks:
                            if str(row.get("job_id") or "").strip() != key:
                                remaining.append(row)
                                continue
                            now = _now_iso()
                            row["delivered_at"] = now
                            row["updated_at"] = now
                            if detail:
                                row["delivery_detail"] = str(detail)[:400]
                            archived = dict(row)

                        if archived is None:
                            continue

                        payload["tasks"] = remaining
                        self._write_payload_unlocked(task_path, payload)
                        self._append_task_history_unlocked(history_path, archived)
                        return True
                except TimeoutError:
                    continue
        return False


worker_task_file_store = WorkerTaskFileStore()
