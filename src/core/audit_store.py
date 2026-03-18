import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List
from uuid import uuid4

from core.config import DATA_DIR


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


class AuditStore:
    """Versioned file snapshots with bounded version retention."""

    def __init__(self):
        kernel_root = (Path(DATA_DIR) / "kernel").resolve()
        self.audit_root = (kernel_root / "audit").resolve()
        self.versions_root = (kernel_root / "versions").resolve()
        self.index_root = (self.audit_root / "index").resolve()
        self.logs_root = (self.audit_root / "logs").resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.versions_root.mkdir(parents=True, exist_ok=True)
        self.index_root.mkdir(parents=True, exist_ok=True)
        self.logs_root.mkdir(parents=True, exist_ok=True)
        self.events_path = (self.audit_root / "events.jsonl").resolve()
        try:
            self.version_retention_count = int(
                os.getenv("AUDIT_VERSION_RETENTION_COUNT", "20")
            )
        except Exception:
            self.version_retention_count = 20
        try:
            self.log_retention_days = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "30"))
        except Exception:
            self.log_retention_days = 30
        self.version_retention_count = max(1, self.version_retention_count)
        self.log_retention_days = max(1, self.log_retention_days)
        self._lock = Lock()
        self._legacy_migrated = False

    @staticmethod
    def _safe_rel_key(path: Path) -> str:
        text = str(path.resolve())
        text = text.replace(os.sep, "__").replace(":", "_")
        return text.strip("_") or "unknown"

    def _history_dir(self, path: Path) -> Path:
        target = (self.versions_root / self._safe_rel_key(path)).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _index_path(self, path: Path) -> Path:
        return (self.index_root / f"{self._safe_rel_key(path)}.json").resolve()

    def _log_path_for_ts(self, ts: str) -> Path:
        parsed = _parse_iso(ts) or datetime.now().astimezone()
        return (self.logs_root / f"{parsed.date().isoformat()}.jsonl").resolve()

    def _read_index_unlocked(self, path: Path) -> List[Dict[str, Any]]:
        index_path = self._index_path(path)
        if not index_path.exists():
            return []
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(loaded, dict):
            rows = loaded.get("versions")
        else:
            rows = loaded
        if not isinstance(rows, list):
            return []
        return [item for item in rows if isinstance(item, dict)]

    def _write_index_unlocked(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        payload = {
            "target": str(path),
            "versions": rows,
        }
        self.index_root.mkdir(parents=True, exist_ok=True)
        self._index_path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _append_log_unlocked(self, payload: Dict[str, Any]) -> None:
        ts = str(payload.get("ts") or _now_iso()).strip() or _now_iso()
        log_path = self._log_path_for_ts(ts)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _prune_versions_unlocked(
        self, path: Path, rows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        existing: List[Dict[str, Any]] = []
        for row in rows:
            snapshot_path = Path(str(row.get("snapshot_path") or "").strip())
            if not snapshot_path.exists():
                continue
            existing.append(dict(row))
        existing.sort(
            key=lambda item: (
                str(item.get("ts") or ""),
                str(item.get("version_id") or ""),
            ),
            reverse=True,
        )
        keep = existing[: self.version_retention_count]
        keep_snapshot_paths = {
            str(item.get("snapshot_path") or "").strip() for item in keep if item
        }
        for row in existing[self.version_retention_count :]:
            snapshot_path = Path(str(row.get("snapshot_path") or "").strip())
            if str(snapshot_path) in keep_snapshot_paths:
                continue
            try:
                snapshot_path.unlink()
            except FileNotFoundError:
                continue
            except Exception:
                pass
        return keep

    def _snapshot_file_unlocked(
        self,
        path: Path,
        *,
        actor: str = "system",
        reason: str = "",
        category: str = "generic",
    ) -> Dict[str, Any]:
        if not path.exists():
            return {}

        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        version_id = f"{stamp}-{uuid4().hex[:8]}"
        history_dir = self._history_dir(path)
        snapshot_path = (history_dir / f"{version_id}.bak").resolve()
        snapshot_path.write_bytes(path.read_bytes())
        entry = {
            "ts": _now_iso(),
            "event": "snapshot",
            "category": category,
            "actor": str(actor or "system"),
            "reason": str(reason or ""),
            "target": str(path),
            "version_id": version_id,
            "snapshot_path": str(snapshot_path),
        }
        rows = self._read_index_unlocked(path)
        rows.append(entry)
        rows = self._prune_versions_unlocked(path, rows)
        self._write_index_unlocked(path, rows)
        self._append_log_unlocked(entry)
        return entry

    def _cleanup_old_logs_unlocked(self) -> None:
        cutoff = datetime.now().astimezone().date() - timedelta(
            days=self.log_retention_days
        )
        for path in self.logs_root.glob("*.jsonl"):
            stem = path.stem
            try:
                target_day = datetime.strptime(stem, "%Y-%m-%d").date()
            except Exception:
                continue
            if target_day >= cutoff:
                continue
            try:
                path.unlink()
            except Exception:
                continue

    def _migrate_legacy_events_unlocked(self) -> None:
        if self._legacy_migrated:
            return
        self._legacy_migrated = True
        if not self.events_path.exists():
            return

        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for raw in lines:
            text = raw.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except Exception:
                continue
            if str(item.get("event") or "").strip() != "snapshot":
                continue
            target = str(item.get("target") or "").strip()
            snapshot_path = Path(str(item.get("snapshot_path") or "").strip())
            if not target or not snapshot_path.exists():
                continue
            grouped.setdefault(target, []).append(dict(item))

        for target, rows in grouped.items():
            target_path = Path(target).resolve()
            existing_rows = self._read_index_unlocked(target_path)
            existing_ids = {
                str(item.get("version_id") or "").strip() for item in existing_rows
            }
            for row in rows:
                version_id = str(row.get("version_id") or "").strip()
                if not version_id or version_id in existing_ids:
                    continue
                existing_rows.append(row)
            existing_rows = self._prune_versions_unlocked(target_path, existing_rows)
            self._write_index_unlocked(target_path, existing_rows)

        try:
            if self.events_path.stat().st_size <= 0:
                self.events_path.unlink(missing_ok=True)
            else:
                target = (
                    self.logs_root
                    / f"legacy-{datetime.now().strftime('%Y%m%d%H%M%S')}.jsonl"
                ).resolve()
                self.events_path.replace(target)
        except Exception:
            pass

    def maintain(self) -> None:
        with self._lock:
            self._migrate_legacy_events_unlocked()
            for index_path in self.index_root.glob("*.json"):
                try:
                    loaded = json.loads(index_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(loaded, dict):
                    target = str(loaded.get("target") or "").strip()
                    rows = loaded.get("versions")
                else:
                    target = ""
                    rows = loaded
                if not target or not isinstance(rows, list):
                    continue
                target_path = Path(target).resolve()
                compacted = self._prune_versions_unlocked(
                    target_path,
                    [item for item in rows if isinstance(item, dict)],
                )
                self._write_index_unlocked(target_path, compacted)
            self._cleanup_old_logs_unlocked()

    def snapshot_file(
        self,
        path: str | Path,
        *,
        actor: str = "system",
        reason: str = "",
        category: str = "generic",
    ) -> str:
        target = Path(path).resolve()
        with self._lock:
            self._migrate_legacy_events_unlocked()
            entry = self._snapshot_file_unlocked(
                target,
                actor=actor,
                reason=reason,
                category=category,
            )
            self._cleanup_old_logs_unlocked()
        return str(entry.get("version_id") or "").strip()

    def write_versioned(
        self,
        path: str | Path,
        content: str,
        *,
        actor: str = "system",
        reason: str = "",
        category: str = "generic",
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            self._migrate_legacy_events_unlocked()
            previous_version = ""
            if target.exists():
                snapshot_entry = self._snapshot_file_unlocked(
                    target,
                    actor=actor,
                    reason=f"pre-write:{reason}",
                    category=category,
                )
                previous_version = str(snapshot_entry.get("version_id") or "").strip()
            target.write_text(content, encoding=encoding)
            self._append_log_unlocked(
                {
                    "ts": _now_iso(),
                    "event": "write",
                    "category": category,
                    "actor": str(actor or "system"),
                    "reason": str(reason or ""),
                    "target": str(target),
                    "previous_version_id": previous_version,
                    "size": len(content.encode(encoding, errors="ignore")),
                }
            )
            self._cleanup_old_logs_unlocked()
            return {
                "ok": True,
                "target": str(target),
                "previous_version_id": previous_version,
            }

    def rollback(
        self,
        path: str | Path,
        version_id: str,
        *,
        actor: str = "system",
        reason: str = "rollback",
    ) -> bool:
        target = Path(path).resolve()
        prefix = str(version_id or "").strip()
        if not prefix:
            return False

        with self._lock:
            self._migrate_legacy_events_unlocked()
            rows = self._prune_versions_unlocked(target, self._read_index_unlocked(target))
            self._write_index_unlocked(target, rows)
            snapshot_row = next(
                (
                    item
                    for item in rows
                    if str(item.get("version_id") or "").strip().startswith(prefix)
                ),
                None,
            )
            if not snapshot_row:
                return False
            snapshot = Path(str(snapshot_row.get("snapshot_path") or "").strip())
            if not snapshot.exists():
                compacted = [
                    item
                    for item in rows
                    if str(item.get("snapshot_path") or "").strip() != str(snapshot)
                ]
                self._write_index_unlocked(target, compacted)
                return False

            current_backup = ""
            if target.exists():
                backup_entry = self._snapshot_file_unlocked(
                    target,
                    actor=actor,
                    reason=f"pre-rollback:{reason}",
                    category="rollback",
                )
                current_backup = str(backup_entry.get("version_id") or "").strip()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(snapshot.read_bytes())
            self._append_log_unlocked(
                {
                    "ts": _now_iso(),
                    "event": "rollback",
                    "actor": str(actor or "system"),
                    "reason": str(reason or "rollback"),
                    "target": str(target),
                    "restored_version_id": str(snapshot_row.get("version_id") or "").strip(),
                    "snapshot_path": str(snapshot),
                    "previous_version_id": current_backup,
                }
            )
            self._cleanup_old_logs_unlocked()
        return True

    def list_versions(self, path: str | Path, limit: int = 20) -> List[Dict[str, Any]]:
        target = Path(path).resolve()
        with self._lock:
            self._migrate_legacy_events_unlocked()
            rows = self._prune_versions_unlocked(target, self._read_index_unlocked(target))
            self._write_index_unlocked(target, rows)
            return rows[: max(1, int(limit))]


audit_store = AuditStore()
