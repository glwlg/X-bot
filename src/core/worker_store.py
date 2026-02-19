import asyncio
import json
import os
import re
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.config import DATA_DIR
from core.tool_access_store import tool_access_store


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slugify(value: str, fallback: str = "worker") -> str:
    raw = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9_\-]+", "-", raw).strip("-")
    return slug or fallback


def _name_from_worker_soul(workspace_root: str) -> str:
    root = str(workspace_root or "").strip()
    if not root:
        return ""
    path = (Path(root) / "SOUL.MD").resolve()
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""

    for line in text.splitlines()[:120]:
        match = re.match(r"^\s*(?:[-*]\s*)?Name\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if not match:
            continue
        name = str(match.group(1) or "").strip().strip("`*")
        if name:
            return name
    return ""


def _resolve_worker_display_name(
    *, worker_id: str, stored_name: str, workspace_root: str
) -> str:
    current = str(stored_name or "").strip()
    soul_name = _name_from_worker_soul(workspace_root)
    if soul_name and (
        not current
        or current == worker_id
        or current.lower() in {"main worker", "worker", "default worker"}
    ):
        return soul_name
    return current or soul_name or worker_id


def _normalize_capabilities(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized: List[str] = []
    for item in value:
        token = str(item or "").strip()
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def _build_worker_summary(
    *,
    worker_id: str,
    worker_name: str,
    backend: str,
    capabilities: List[str],
    stored_summary: str,
) -> str:
    summary = str(stored_summary or "").strip()
    if summary:
        return summary
    if capabilities:
        capability_hint = "、".join(capabilities[:4])
        return f"{worker_name or worker_id} 是通用执行助手，擅长：{capability_hint}。"
    backend_name = str(backend or "core-agent").strip() or "core-agent"
    return f"{worker_name or worker_id} 是通用执行助手（backend={backend_name}），可处理跨工具任务。"


def _normalize_worker_output(
    output: Any,
    *,
    result: Any = "",
    result_summary: str = "",
    error: str = "",
) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    if isinstance(output, dict):
        normalized.update(output)

    text_candidates = [
        normalized.get("text"),
        result,
        result_summary,
    ]
    for value in text_candidates:
        text = str(value or "").strip()
        if text:
            normalized["text"] = text
            break

    if "error" not in normalized:
        error_text = str(error or "").strip()
        if error_text:
            normalized["error"] = error_text

    ui_payload = normalized.get("ui")
    if not isinstance(ui_payload, dict):
        normalized.pop("ui", None)
    return normalized


class WorkerRegistry:
    """Persistent worker registry for userland workers."""

    def __init__(self):
        base_root = os.getenv("USERLAND_ROOT", "").strip()
        if base_root:
            self.root = Path(base_root).resolve()
        else:
            self.root = (Path(DATA_DIR) / "userland" / "workers").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

        self.meta_path = (Path(DATA_DIR) / "WORKERS.json").resolve()
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)

        self.default_backend = (
            os.getenv("WORKER_DEFAULT_BACKEND", "core-agent").strip().lower()
            or "core-agent"
        )
        self._lock = asyncio.Lock()

    def _default_payload(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": _now_iso(),
            "workers": {},
        }

    def _read_unlocked(self) -> Dict[str, Any]:
        if not self.meta_path.exists():
            data = self._default_payload()
            self.meta_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return data
        try:
            loaded = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                loaded = {}
        except Exception:
            loaded = {}
        data = self._default_payload()
        data.update(loaded)
        workers = data.get("workers")
        if not isinstance(workers, dict):
            workers = {}
        normalized: Dict[str, Any] = {}
        for key, value in workers.items():
            if not isinstance(value, dict):
                continue
            worker_id = _slugify(str(key), fallback="worker")
            default_workspace = str((self.root / worker_id).resolve())
            configured_workspace = str(value.get("workspace_root") or "").strip()
            workspace_root = configured_workspace or default_workspace
            if configured_workspace:
                configured_exists = Path(configured_workspace).exists()
                default_exists = Path(default_workspace).exists()
                if not configured_exists and default_exists:
                    workspace_root = default_workspace
            worker_name = _resolve_worker_display_name(
                worker_id=worker_id,
                stored_name=str(value.get("name") or "").strip(),
                workspace_root=workspace_root,
            )
            backend_name = (
                str(value.get("backend") or self.default_backend).strip().lower()
                or self.default_backend
            )
            capabilities = _normalize_capabilities(value.get("capabilities"))
            record = {
                "id": worker_id,
                "name": worker_name,
                "backend": backend_name,
                "status": str(value.get("status") or "ready").strip().lower()
                or "ready",
                "capabilities": capabilities,
                "summary": _build_worker_summary(
                    worker_id=worker_id,
                    worker_name=worker_name,
                    backend=backend_name,
                    capabilities=capabilities,
                    stored_summary=str(value.get("summary") or ""),
                ),
                "workspace_root": workspace_root,
                "credentials_root": str(
                    value.get("credentials_root")
                    or (Path(DATA_DIR) / "credentials" / "workers" / worker_id)
                ).strip(),
                "created_at": str(value.get("created_at") or _now_iso()),
                "updated_at": str(value.get("updated_at") or _now_iso()),
                "last_task_id": str(value.get("last_task_id") or ""),
                "last_error": str(value.get("last_error") or ""),
                "auth": value.get("auth")
                if isinstance(value.get("auth"), dict)
                else {},
            }
            normalized[worker_id] = record
        data["workers"] = normalized
        data["updated_at"] = str(data.get("updated_at") or _now_iso())
        return data

    def _write_unlocked(self, data: Dict[str, Any]) -> None:
        payload = self._default_payload()
        payload.update(data or {})
        payload["updated_at"] = _now_iso()
        self.meta_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def ensure_default_worker(
        self, worker_id: str = "worker-main"
    ) -> Dict[str, Any]:
        async with self._lock:
            data = self._read_unlocked()
            workers = data["workers"]
            safe_id = _slugify(worker_id, fallback="worker-main")
            existing = workers.get(safe_id)
            if existing:
                tool_access_store.ensure_worker_policy(safe_id)
                return dict(existing)
            created = self._build_worker_record(
                worker_id=safe_id,
                name="Main Worker",
                backend=self.default_backend,
            )
            workers[safe_id] = created
            self._ensure_worker_dirs(created)
            self._write_unlocked(data)
            tool_access_store.ensure_worker_policy(safe_id)
            return dict(created)

    async def list_workers(self) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._read_unlocked()
            workers = list((data.get("workers") or {}).values())
            return sorted(workers, key=lambda item: item.get("created_at", ""))

    async def get_worker(self, worker_id: str) -> Dict[str, Any] | None:
        safe_id = _slugify(worker_id, fallback="")
        if not safe_id:
            return None
        async with self._lock:
            data = self._read_unlocked()
            record = (data.get("workers") or {}).get(safe_id)
            if not isinstance(record, dict):
                return None
            return dict(record)

    def _build_worker_record(
        self, worker_id: str, name: str, backend: str
    ) -> Dict[str, Any]:
        now = _now_iso()
        safe_id = _slugify(worker_id, fallback="worker")
        return {
            "id": safe_id,
            "name": str(name or safe_id).strip() or safe_id,
            "backend": str(backend or self.default_backend).strip().lower()
            or self.default_backend,
            "status": "ready",
            "capabilities": [],  # 运行时可动态更新
            "summary": f"{str(name or safe_id).strip() or safe_id} 是通用执行助手，可处理跨工具任务。",
            "workspace_root": str((self.root / safe_id).resolve()),
            "credentials_root": str(
                (Path(DATA_DIR) / "credentials" / "workers" / safe_id).resolve()
            ),
            "created_at": now,
            "updated_at": now,
            "last_task_id": "",
            "last_error": "",
            "auth": {},
        }

    def _ensure_worker_dirs(self, worker: Dict[str, Any]) -> None:
        Path(str(worker["workspace_root"])).mkdir(parents=True, exist_ok=True)
        Path(str(worker["credentials_root"])).mkdir(parents=True, exist_ok=True)

    async def create_worker(
        self,
        name: str,
        *,
        backend: str | None = None,
        worker_id: str | None = None,
    ) -> Dict[str, Any]:
        async with self._lock:
            data = self._read_unlocked()
            workers = data["workers"]
            base_id = _slugify(worker_id or name, fallback="worker")
            final_id = base_id
            suffix = 1
            while final_id in workers:
                suffix += 1
                final_id = f"{base_id}-{suffix}"
            record = self._build_worker_record(
                final_id, name=name, backend=backend or self.default_backend
            )
            workers[final_id] = record
            self._ensure_worker_dirs(record)
            self._write_unlocked(data)
            tool_access_store.ensure_worker_policy(final_id)
            return dict(record)

    async def delete_worker(self, worker_id: str) -> bool:
        safe_id = _slugify(worker_id, fallback="")
        if not safe_id:
            return False
        async with self._lock:
            data = self._read_unlocked()
            workers = data.get("workers") or {}
            if safe_id not in workers:
                return False
            del workers[safe_id]
            self._write_unlocked(data)
            tool_access_store.reset_worker_policy(safe_id)
            return True

    async def update_worker(self, worker_id: str, **fields) -> Dict[str, Any] | None:
        safe_id = _slugify(worker_id, fallback="")
        if not safe_id:
            return None
        async with self._lock:
            data = self._read_unlocked()
            workers = data.get("workers") or {}
            current = workers.get(safe_id)
            if not isinstance(current, dict):
                return None
            for key in (
                "name",
                "status",
                "backend",
                "summary",
                "last_task_id",
                "last_error",
            ):
                if key in fields:
                    current[key] = str(
                        fields[key] if fields[key] is not None else current.get(key, "")
                    ).strip()
            if "auth" in fields and isinstance(fields["auth"], dict):
                current["auth"] = fields["auth"]
            if "capabilities" in fields and isinstance(fields["capabilities"], list):
                current["capabilities"] = fields["capabilities"]
            current["updated_at"] = _now_iso()
            workers[safe_id] = current
            self._write_unlocked(data)
            return dict(current)

    async def set_auth_state(
        self, worker_id: str, provider: str, state: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        safe_id = _slugify(worker_id, fallback="")
        if not safe_id:
            return None
        async with self._lock:
            data = self._read_unlocked()
            workers = data.get("workers") or {}
            current = workers.get(safe_id)
            if not isinstance(current, dict):
                return None
            auth = current.get("auth")
            if not isinstance(auth, dict):
                auth = {}
            auth[str(provider).strip().lower()] = state
            current["auth"] = auth
            current["updated_at"] = _now_iso()
            workers[safe_id] = current
            self._write_unlocked(data)
            return dict(current)


class WorkerTaskStore:
    """Append-only worker task store (JSONL)."""

    def __init__(self):
        self.path = (Path(DATA_DIR) / "WORKER_TASKS.jsonl").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _now(self) -> str:
        return _now_iso()

    @staticmethod
    def _normalize_source(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"user", "user_cmd", "command", "cmd"}:
            return "user_cmd"
        if raw in {"user_chat", "chat", "message"}:
            return "user_chat"
        if raw in {"heartbeat", "hb"}:
            return "heartbeat"
        if raw in {"system", "internal"}:
            return "system"
        return "system"

    def _new_event(
        self,
        *,
        task_id: str,
        source: str,
        status: str,
        retry_count: int,
        error: str = "",
        detail: str = "",
    ) -> Dict[str, Any]:
        return {
            "at": self._now(),
            "task_id": str(task_id),
            "source": self._normalize_source(source),
            "status": str(status or "").strip().lower(),
            "retry": max(0, int(retry_count)),
            "error": str(error or "").strip()[:400],
            "detail": str(detail or "").strip()[:400],
        }

    async def create_task(
        self,
        *,
        worker_id: str,
        source: str,
        instruction: str,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_source = self._normalize_source(source)
        created_at = self._now()
        record = {
            "task_id": f"wt-{int(datetime.now().timestamp())}-{uuid4().hex[:8]}",
            "worker_id": _slugify(worker_id, fallback="worker-main"),
            "source": normalized_source,
            "instruction": str(instruction or "").strip(),
            "status": "queued",
            "result_summary": "",
            "result": "",
            "error": "",
            "retry_count": 0,
            "trace_id": f"trace-{int(datetime.now().timestamp())}",
            "created_at": created_at,
            "started_at": "",
            "ended_at": "",
            "metadata": metadata or {},
            "output": {},
            "events": [],
        }
        record["events"].append(
            self._new_event(
                task_id=record["task_id"],
                source=normalized_source,
                status="queued",
                retry_count=0,
                detail="task created",
            )
        )
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return dict(record)

    async def _read_all_unlocked(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            text = raw.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
                if isinstance(row, dict):
                    row["output"] = _normalize_worker_output(
                        row.get("output"),
                        result=row.get("result"),
                        result_summary=str(row.get("result_summary") or ""),
                        error=str(row.get("error") or ""),
                    )
                    rows.append(row)
            except Exception:
                continue
        return rows

    async def update_task(self, task_id: str, **fields) -> Dict[str, Any] | None:
        async with self._lock:
            rows = await self._read_all_unlocked()
            changed = None
            for row in rows:
                if str(row.get("task_id")) != str(task_id):
                    continue
                previous_status = str(row.get("status", "")).strip().lower()
                previous_retry = int(row.get("retry_count", 0) or 0)
                for key in (
                    "status",
                    "result_summary",
                    "result",
                    "output",
                    "error",
                    "started_at",
                    "ended_at",
                    "retry_count",
                ):
                    if key in fields:
                        row[key] = fields[key]
                if "source" in fields:
                    row["source"] = self._normalize_source(fields["source"])
                if "retry_count" in fields:
                    try:
                        row["retry_count"] = max(0, int(fields["retry_count"]))
                    except Exception:
                        row["retry_count"] = previous_retry
                row["output"] = _normalize_worker_output(
                    row.get("output"),
                    result=row.get("result"),
                    result_summary=str(row.get("result_summary") or ""),
                    error=str(row.get("error") or ""),
                )

                events = row.get("events")
                if not isinstance(events, list):
                    events = []
                current_status = str(row.get("status", "")).strip().lower()
                current_retry = int(row.get("retry_count", 0) or 0)
                state_changed = current_status and current_status != previous_status
                retry_changed = current_retry != previous_retry
                error_text = str(row.get("error") or "")
                if state_changed or retry_changed or ("error" in fields and error_text):
                    events.append(
                        self._new_event(
                            task_id=task_id,
                            source=str(row.get("source") or "system"),
                            status=current_status or previous_status or "running",
                            retry_count=current_retry,
                            error=error_text,
                            detail=str(fields.get("result_summary") or "")[:180],
                        )
                    )
                row["events"] = events[-40:]
                changed = dict(row)
                break
            if changed is None:
                return None
            with self.path.open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return changed

    async def get_task(self, task_id: str) -> Dict[str, Any] | None:
        async with self._lock:
            rows = await self._read_all_unlocked()
            for row in rows:
                if str(row.get("task_id")) == str(task_id):
                    return dict(row)
        return None

    async def list_recent(
        self,
        worker_id: str | None = None,
        limit: int = 20,
        include_sources: List[str] | None = None,
        exclude_sources: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        async with self._lock:
            rows = await self._read_all_unlocked()
        if worker_id:
            safe_id = _slugify(worker_id, fallback="")
            rows = [row for row in rows if str(row.get("worker_id")) == safe_id]
        if include_sources:
            allowed = {
                self._normalize_source(item)
                for item in include_sources
                if str(item).strip()
            }
            rows = [
                row
                for row in rows
                if self._normalize_source(str(row.get("source") or "")) in allowed
            ]
        if exclude_sources:
            blocked = {
                self._normalize_source(item)
                for item in exclude_sources
                if str(item).strip()
            }
            rows = [
                row
                for row in rows
                if self._normalize_source(str(row.get("source") or "")) not in blocked
            ]
        return rows[-max(1, int(limit)) :]

    async def list_recent_outputs(
        self,
        *,
        worker_id: str | None = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        rows = await self.list_recent(worker_id=worker_id, limit=limit)
        outputs: List[Dict[str, Any]] = []
        for row in rows:
            outputs.append(
                {
                    "task_id": str(row.get("task_id") or ""),
                    "worker_id": str(row.get("worker_id") or ""),
                    "status": str(row.get("status") or ""),
                    "ended_at": str(row.get("ended_at") or ""),
                    "output": dict(row.get("output") or {}),
                }
            )
        return outputs


worker_registry = WorkerRegistry()
worker_task_store = WorkerTaskStore()
