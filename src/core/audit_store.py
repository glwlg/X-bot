import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List
from uuid import uuid4

from core.config import DATA_DIR


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class AuditStore:
    """Versioned file snapshots + append-only audit log for kernel artifacts."""

    def __init__(self):
        kernel_root = (Path(DATA_DIR) / "kernel").resolve()
        self.audit_root = (kernel_root / "audit").resolve()
        self.versions_root = (kernel_root / "versions").resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.versions_root.mkdir(parents=True, exist_ok=True)
        self.events_path = (self.audit_root / "events.jsonl").resolve()
        self._lock = Lock()

    @staticmethod
    def _safe_rel_key(path: Path) -> str:
        text = str(path.resolve())
        text = text.replace(os.sep, "__").replace(":", "_")
        return text.strip("_") or "unknown"

    def _history_dir(self, path: Path) -> Path:
        target = (self.versions_root / self._safe_rel_key(path)).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _append_event_unlocked(self, payload: Dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def snapshot_file(
        self,
        path: str | Path,
        *,
        actor: str = "system",
        reason: str = "",
        category: str = "generic",
    ) -> str:
        target = Path(path).resolve()
        if not target.exists():
            return ""

        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        version_id = f"{stamp}-{uuid4().hex[:8]}"
        history_dir = self._history_dir(target)
        snapshot_path = (history_dir / f"{version_id}.bak").resolve()
        snapshot_path.write_bytes(target.read_bytes())
        self._append_event_unlocked(
            {
                "ts": _now_iso(),
                "event": "snapshot",
                "category": category,
                "actor": str(actor or "system"),
                "reason": str(reason or ""),
                "target": str(target),
                "version_id": version_id,
                "snapshot_path": str(snapshot_path),
            }
        )
        return version_id

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
            previous_version = ""
            if target.exists():
                previous_version = self.snapshot_file(
                    target,
                    actor=actor,
                    reason=f"pre-write:{reason}",
                    category=category,
                )
            target.write_text(content, encoding=encoding)
            self._append_event_unlocked(
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
        history_dir = self._history_dir(target)
        prefix = str(version_id or "").strip()
        if not prefix:
            return False

        candidates = sorted(history_dir.glob(f"{prefix}*.bak"))
        if not candidates:
            return False

        snapshot = candidates[-1]
        with self._lock:
            current_backup = ""
            if target.exists():
                current_backup = self.snapshot_file(
                    target,
                    actor=actor,
                    reason=f"pre-rollback:{reason}",
                    category="rollback",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(snapshot.read_bytes())
            self._append_event_unlocked(
                {
                    "ts": _now_iso(),
                    "event": "rollback",
                    "actor": str(actor or "system"),
                    "reason": str(reason or "rollback"),
                    "target": str(target),
                    "restored_version_id": prefix,
                    "snapshot_path": str(snapshot),
                    "previous_version_id": current_backup,
                }
            )
        return True

    def list_versions(self, path: str | Path, limit: int = 20) -> List[Dict[str, Any]]:
        target = str(Path(path).resolve())
        if not self.events_path.exists():
            return []

        rows: List[Dict[str, Any]] = []
        for raw in reversed(self.events_path.read_text(encoding="utf-8").splitlines()):
            text = raw.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except Exception:
                continue
            if str(item.get("target", "")) != target:
                continue
            if item.get("event") != "snapshot":
                continue
            rows.append(item)
            if len(rows) >= max(1, int(limit)):
                break
        return rows


audit_store = AuditStore()
